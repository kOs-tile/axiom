"""
AXIOM Permission Checker — AST-based static analysis for generated skill code.

Walks the Python AST of generated skill code and rejects:
- Unauthorised imports (network, OS, subprocess, file I/O)
- Direct file system access (open(), os.path, pathlib)
- Subprocess or shell invocation
- Dynamic code execution (__import__, eval, exec, compile)
- Network socket usage
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


# ── Allowlists ────────────────────────────────────────────────────────────────

ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        # Standard library — safe
        "math",
        "statistics",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "operator",
        "re",
        "string",
        "textwrap",
        "json",
        "decimal",
        "fractions",
        "random",
        "uuid",
        "typing",
        "types",
        "enum",
        "abc",
        "dataclasses",
        "copy",
        "pprint",
        "hashlib",
        "base64",
        "struct",
        "io",  # BytesIO / StringIO allowed, not file open
        "time",
        "calendar",
        # Data processing — safe
        "numpy",
        "pandas",
        "scipy",
        # Formatting
        "tabulate",
        "jinja2",
    }
)

FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "ssl",
        "http",
        "urllib",
        "urllib2",
        "urllib3",
        "requests",
        "httpx",
        "aiohttp",
        "asyncio",  # allow basic asyncio but block subprocess specifically
        "pathlib",
        "shutil",
        "glob",
        "tempfile",
        "ftplib",
        "smtplib",
        "imaplib",
        "xmlrpc",
        "multiprocessing",
        "concurrent",
        "threading",
        "ctypes",
        "cffi",
        "mmap",
        "signal",
        "pty",
        "termios",
        "grp",
        "pwd",
        "resource",
        "pickle",
        "shelve",
        "marshal",
        "importlib",
        "pkgutil",
        "zipimport",
        "runpy",
        "builtins",
        "__builtin__",
    }
)

FORBIDDEN_BUILTINS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "input",
        "breakpoint",
    }
)

FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__class__",
        "__bases__",
        "__subclasses__",
        "__code__",
        "__globals__",
        "__builtins__",
        "func_globals",
        "gi_frame",
        "f_locals",
        "f_globals",
    }
)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class PermissionViolation:
    kind: str  # "forbidden_import" | "forbidden_call" | "forbidden_attr" | "syntax_error"
    description: str
    line_number: int = 0
    code_snippet: str = ""


@dataclass
class PermissionCheckResult:
    passed: bool
    violations: list[PermissionViolation] = field(default_factory=list)
    warning_count: int = 0


# ── AST Visitor ───────────────────────────────────────────────────────────────

class _PermissionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[PermissionViolation] = []
        self.warnings: int = 0

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top_module = alias.name.split(".")[0]
            self._check_module(top_module, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            top_module = node.module.split(".")[0]
            self._check_module(top_module, node.lineno)
        self.generic_visit(node)

    def _check_module(self, module: str, lineno: int) -> None:
        if module in FORBIDDEN_MODULES:
            self.violations.append(
                PermissionViolation(
                    kind="forbidden_import",
                    description=f"Import of forbidden module '{module}' is not allowed",
                    line_number=lineno,
                )
            )
        elif module not in ALLOWED_IMPORTS:
            # Not in explicit allowlist — warn but don't hard fail
            self.warnings += 1
            logger.debug(f"Unlisted module import '{module}' at line {lineno} — warning only")

    def visit_Call(self, node: ast.Call) -> None:
        # Check direct forbidden builtin calls: eval(...), exec(...), etc.
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTINS:
            self.violations.append(
                PermissionViolation(
                    kind="forbidden_call",
                    description=f"Call to forbidden builtin '{func.id}' detected",
                    line_number=node.lineno,
                )
            )
        # Check __import__ call
        if isinstance(func, ast.Name) and func.id == "__import__":
            self.violations.append(
                PermissionViolation(
                    kind="forbidden_call",
                    description="Dynamic __import__() call detected",
                    line_number=node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRIBUTES:
            self.violations.append(
                PermissionViolation(
                    kind="forbidden_attr",
                    description=f"Access to forbidden attribute '{node.attr}' detected",
                    line_number=node.lineno,
                )
            )
        self.generic_visit(node)


# ── Public API ─────────────────────────────────────────────────────────────────

class PermissionChecker:
    """
    Runs AST-based permission analysis on generated skill code.
    """

    def check(self, code: str, skill_name: str = "unknown") -> PermissionCheckResult:
        """
        Parse and walk the AST of `code`.
        Returns PermissionCheckResult with violations list.
        """
        logger.debug(f"AST permission check for skill '{skill_name}'")

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return PermissionCheckResult(
                passed=False,
                violations=[
                    PermissionViolation(
                        kind="syntax_error",
                        description=f"Syntax error: {exc.msg}",
                        line_number=exc.lineno or 0,
                    )
                ],
            )

        visitor = _PermissionVisitor()
        visitor.visit(tree)

        passed = len(visitor.violations) == 0
        if not passed:
            logger.warning(
                f"Permission check FAILED for '{skill_name}': "
                f"{len(visitor.violations)} violation(s)"
            )
        else:
            logger.debug(f"Permission check PASSED for '{skill_name}'")

        return PermissionCheckResult(
            passed=passed,
            violations=visitor.violations,
            warning_count=visitor.warnings,
        )


# Module-level singleton
permission_checker = PermissionChecker()
