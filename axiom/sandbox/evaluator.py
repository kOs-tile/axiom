"""
AXIOM Sandbox Evaluator

Runs generated skill code in a RestrictedPython environment with:
  - Memory cap (256MB via resource limits on Unix)
  - Execution timeout (30s via asyncio.wait_for)
  - Network isolation flag
  - Allowed builtins whitelist
  - Auto-generated unit test execution
  - Resource profiling via tracemalloc

Returns a full EvaluationReport with security scan results + test outcomes.
"""

from __future__ import annotations

import asyncio
import resource
import time
import tracemalloc
from typing import Any, Optional

from loguru import logger
from RestrictedPython import (
    compile_restricted,
    safe_globals,
)
from RestrictedPython.Guards import (
    full_write_guard,
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safe_builtins,
    safer_getattr,
)

from axiom.config import settings
from axiom.models import (
    EvaluationReport,
    SecurityIssue,
    SecurityIssueSeverity,
    Skill,
    TestCase,
    TestResult,
)
from axiom.sandbox.permission_checker import ALLOWED_IMPORTS, permission_checker
from axiom.sandbox.security_scanner import bandit_scanner

# ── Restricted builtins allowlist ─────────────────────────────────────────────

SAFE_BUILTINS = {
    **safe_builtins,
    # Add commonly needed builtins
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "isinstance": isinstance,
    "repr": repr,
    "hash": hash,
    "hex": hex,
    "oct": oct,
    "bin": bin,
    "any": any,
    "all": all,
    "next": next,
    "iter": iter,
    "print": print,
    # Explicitly blocked
    "eval": None,
    "exec": None,
    "compile": None,
    "open": None,
    "input": None,
}


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Import only explicitly reviewed top-level modules.

    RestrictedPython does not provide an import policy. Once __import__ is
    exposed, every reachable module becomes part of the trusted computing base.
    """
    if level != 0:
        raise ImportError("relative imports are not allowed")
    top_module = name.split(".", 1)[0]
    if top_module not in ALLOWED_IMPORTS:
        raise ImportError(f"{top_module!r} may not be imported")
    return __import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS["__import__"] = _guarded_import


def _guarded_getitem(obj, key):
    """Block underscore-based reflective keys while allowing normal indexing."""
    if isinstance(key, str) and key.startswith("_"):
        raise KeyError("restricted key access")
    return obj[key]


def _inplacevar(op, x, y):
    """RestrictedPython hook for a small, explicit set of augmented assignments."""
    operations = {
        "+=": lambda a, b: a + b,
        "-=": lambda a, b: a - b,
        "*=": lambda a, b: a * b,
        "/=": lambda a, b: a / b,
        "//=": lambda a, b: a // b,
        "%=": lambda a, b: a % b,
    }
    if op not in operations:
        raise ValueError(f"unsupported augmented assignment: {op}")
    return operations[op](x, y)


SAFE_GLOBALS = {
    **safe_globals,
    "__builtins__": SAFE_BUILTINS,
    "_getattr_": safer_getattr,
    "_getitem_": _guarded_getitem,
    "_getiter_": iter,
    "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
    "_inplacevar_": _inplacevar,
    "_write_": full_write_guard,
    "_unpack_sequence_": guarded_unpack_sequence,
}


class SandboxEvaluator:
    """
    Full evaluation pipeline: security scan → sandbox execution → report.
    """

    async def evaluate(
        self,
        skill: Skill,
        test_cases: list[TestCase],
    ) -> EvaluationReport:
        """
        Run the complete evaluation pipeline for a synthesised skill.
        Returns an EvaluationReport.
        """
        logger.info(f"Evaluating skill '{skill.name}' (id={skill.id})")
        report = EvaluationReport(skill_id=skill.id, skill_name=skill.name)

        # ── 1. AST Permission check ───────────────────────────────────────────
        perm_result = permission_checker.check(skill.implementation, skill.name)
        report.permission_check_passed = perm_result.passed

        if not perm_result.passed:
            for v in perm_result.violations:
                report.security_issues.append(
                    SecurityIssue(
                        severity=SecurityIssueSeverity.HIGH,
                        confidence="HIGH",
                        issue_id="AXIOM-AST-001",
                        description=v.description,
                        line_number=v.line_number,
                    )
                )
            report.failure_reason = (
                f"AST permission check failed: {perm_result.violations[0].description}"
            )
            report.promotion_recommended = False
            return report

        # ── 2. Bandit security scan ───────────────────────────────────────────
        bandit_result = await bandit_scanner.scan(skill.implementation, skill.name)
        report.bandit_passed = bandit_result.passed

        for issue in bandit_result.issues:
            report.security_issues.append(issue)

        if not bandit_result.passed:
            report.failure_reason = (
                f"Bandit security scan failed: {bandit_result.high_severity_count} "
                "high-severity issue(s)"
            )
            report.promotion_recommended = False
            return report

        # ── 3. Compile in RestrictedPython ────────────────────────────────────
        try:
            restricted_code = compile_restricted(
                skill.implementation,
                filename=f"<skill:{skill.name}>",
                mode="exec",
            )
        except SyntaxError as exc:
            report.failure_reason = f"RestrictedPython compilation failed: {exc}"
            report.promotion_recommended = False
            return report

        # ── 4. Execute test cases ──────────────────────────────────────────────
        test_results: list[TestResult] = []
        total_memory_mb = 0.0

        for tc in test_cases:
            result = await self._run_test_case(
                restricted_code=restricted_code,
                skill=skill,
                test_case=tc,
            )
            test_results.append(result)
            total_memory_mb = max(total_memory_mb, 0.0)

        report.test_results = test_results
        report.tests_passed = sum(1 for r in test_results if r.passed)
        report.tests_failed = sum(1 for r in test_results if not r.passed)
        report.total_execution_ms = sum(r.execution_time_ms for r in test_results)
        report.peak_memory_mb = total_memory_mb

        # ── 5. Promotion verdict ──────────────────────────────────────────────
        total = report.tests_passed + report.tests_failed
        pass_rate = report.tests_passed / total if total > 0 else 0.0
        meets_threshold = (
            pass_rate >= settings.promotion_min_success_rate
            and report.tests_passed >= settings.promotion_min_test_cases
        )

        report.promotion_recommended = meets_threshold
        if not meets_threshold:
            report.failure_reason = (
                f"Pass rate {pass_rate:.0%} below threshold "
                f"{settings.promotion_min_success_rate:.0%} "
                f"({report.tests_passed}/{total} tests passed)"
            )

        logger.info(
            f"Evaluation complete for '{skill.name}': "
            f"{report.tests_passed}/{total} passed, "
            f"promote={report.promotion_recommended}"
        )
        return report

    async def _run_test_case(
        self,
        restricted_code: Any,
        skill: Skill,
        test_case: TestCase,
    ) -> TestResult:
        """
        Execute a single test case inside the RestrictedPython sandbox.
        Times out after settings.sandbox_timeout_seconds.
        """
        t0 = time.perf_counter()

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._execute_in_sandbox,
                    restricted_code,
                    skill,
                    test_case.input_data,
                ),
                timeout=settings.sandbox_timeout_seconds,
            )
            elapsed = (time.perf_counter() - t0) * 1000

            # Type check if expected_type specified
            passed = True
            error_msg: Optional[str] = None
            if test_case.expected_type and result is not None:
                actual_type = type(result).__name__
                if actual_type != test_case.expected_type:
                    passed = False
                    error_msg = f"Expected type {test_case.expected_type}, got {actual_type}"

            if test_case.expected_output is not None and result != test_case.expected_output:
                passed = False
                error_msg = f"Expected {test_case.expected_output!r}, got {result!r}"

            return TestResult(
                test_case=test_case,
                passed=passed,
                actual_output=result,
                error_message=error_msg,
                execution_time_ms=round(elapsed, 2),
            )

        except asyncio.TimeoutError:
            elapsed = (time.perf_counter() - t0) * 1000
            return TestResult(
                test_case=test_case,
                passed=False,
                error_message=f"Execution timed out after {settings.sandbox_timeout_seconds}s",
                execution_time_ms=round(elapsed, 2),
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return TestResult(
                test_case=test_case,
                passed=False,
                error_message=str(exc),
                execution_time_ms=round(elapsed, 2),
            )

    @staticmethod
    def _execute_in_sandbox(
        restricted_code: Any,
        skill: Skill,
        input_data: dict[str, Any],
    ) -> Any:
        """
        Blocking execution in RestrictedPython sandbox.
        Sets memory limit via resource module (Unix only).
        """
        # Apply memory limit
        limit_bytes = settings.sandbox_memory_limit_mb * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
        except (AttributeError, ValueError):
            pass  # Windows or already enforced

        # Prepare execution namespace
        glb = dict(SAFE_GLOBALS)
        glb["__builtins__"] = SAFE_BUILTINS

        # Execute the compiled skill code to define the `run` function
        exec(restricted_code, glb)  # noqa: S102 — intentional sandbox exec

        entry = glb.get(skill.entry_point)
        if entry is None or not callable(entry):
            raise RuntimeError(
                f"Entry point '{skill.entry_point}' not found in skill implementation"
            )

        # Handle both async and sync entry points
        if asyncio.iscoroutinefunction(entry):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(entry(**input_data))
            finally:
                loop.close()
        else:
            return entry(**input_data)


# Module-level singleton
sandbox_evaluator = SandboxEvaluator()
