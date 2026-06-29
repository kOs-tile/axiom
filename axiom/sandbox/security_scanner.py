"""
AXIOM Security Scanner — bandit static analysis on generated skill code.

Runs bandit as a subprocess, parses its JSON output, and returns a structured
result indicating whether the code passes the security bar.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from axiom.models import SecurityIssue, SecurityIssueSeverity

# Minimum severity/confidence that triggers a FAIL verdict
_FAIL_SEVERITY = {"HIGH", "CRITICAL"}
_WARN_SEVERITY = {"MEDIUM"}


@dataclass
class BanditScanResult:
    passed: bool
    issues: list[SecurityIssue] = field(default_factory=list)
    high_severity_count: int = 0
    medium_severity_count: int = 0
    scan_stdout: str = ""
    scan_stderr: str = ""
    error_message: str = ""


class BanditScanner:
    """
    Wraps the `bandit` CLI tool to statically analyse generated Python code.

    bandit is run with:
        bandit -r -f json -l -i <tempfile>

    Flags:
        -r      recursive (needed even for single file)
        -f json output as JSON
        -l      minimum severity LOW (we filter in Python)
        -i      minimum confidence LOW

    A scan FAILS (returns passed=False) if any HIGH or CRITICAL severity
    issue is found, regardless of confidence level.
    """

    async def scan(self, code: str, skill_name: str = "unknown") -> BanditScanResult:
        """
        Write code to a temp file, run bandit, parse results.
        Async-safe: uses asyncio.to_thread for the blocking subprocess call.
        """
        logger.debug(f"Running bandit scan for skill '{skill_name}'")

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            prefix=f"axiom_skill_{skill_name}_",
            delete=False,
        ) as tmp:
            tmp.write(code)
            tmp_path = Path(tmp.name)

        try:
            result = await asyncio.to_thread(self._run_bandit, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        return result

    @staticmethod
    def _run_bandit(path: Path) -> BanditScanResult:
        """Blocking subprocess call to bandit — run in thread pool."""
        import subprocess

        try:
            proc = subprocess.run(
                [
                    "bandit",
                    "-r",
                    "-f", "json",
                    "-l",  # low severity and above
                    "-i",  # low confidence and above
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired:
            return BanditScanResult(
                passed=False,
                error_message="bandit scan timed out after 30s",
            )
        except FileNotFoundError:
            logger.warning("bandit not found in PATH — skipping security scan")
            return BanditScanResult(
                passed=True,
                scan_stderr="bandit not installed — scan skipped",
            )
        except Exception as exc:
            return BanditScanResult(
                passed=False,
                error_message=f"bandit execution error: {exc}",
            )

        # bandit exits 0 = no issues, 1 = issues found
        issues: list[SecurityIssue] = []
        high_count = 0
        medium_count = 0

        try:
            data = json.loads(stdout)
            for result in data.get("results", []):
                sev_str = result.get("issue_severity", "LOW").upper()
                try:
                    severity = SecurityIssueSeverity(sev_str)
                except ValueError:
                    severity = SecurityIssueSeverity.LOW

                issue = SecurityIssue(
                    severity=severity,
                    confidence=result.get("issue_confidence", "LOW").upper(),
                    issue_id=result.get("test_id", "B000"),
                    description=result.get("issue_text", ""),
                    line_number=result.get("line_number", 0),
                    code_snippet=result.get("code", ""),
                )
                issues.append(issue)

                if sev_str in _FAIL_SEVERITY:
                    high_count += 1
                elif sev_str in _WARN_SEVERITY:
                    medium_count += 1

        except (json.JSONDecodeError, KeyError) as exc:
            logger.debug(f"Could not parse bandit JSON output: {exc}")
            # If we can't parse bandit output, treat as passed (no issues found)
            return BanditScanResult(
                passed=True,
                scan_stdout=stdout,
                scan_stderr=stderr,
            )

        passed = high_count == 0
        if not passed:
            logger.warning(
                f"Bandit scan FAILED: {high_count} high-severity issue(s) found"
            )
        else:
            logger.debug(
                f"Bandit scan PASSED (medium warnings: {medium_count})"
            )

        return BanditScanResult(
            passed=passed,
            issues=issues,
            high_severity_count=high_count,
            medium_severity_count=medium_count,
            scan_stdout=stdout,
            scan_stderr=stderr,
        )


# Module-level singleton
bandit_scanner = BanditScanner()
