"""What `inspect` reports when a script fails: issues with codes, lines and repair hints.

`issues_for` maps loader and contract exceptions to issues for both `inspect` and `run`.

The loader decides whether a script is valid. When it rejects one, `explain` reads the source as
a syntax tree (it never runs the code) to name the usual newcomer mistakes more precisely.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from .errors import EntryPointError, NoEntryPointError
from .loader import ScriptImportError, ScriptSyntaxError
from .schema import SchemaError

PACKAGE = "mobilerun_functions"

SYNTAX_ERROR = "syntax_error"
IMPORT_WRONG_MODULE = "import_wrong_module"
IMPORT_MISSING = "import_missing"
NO_ENTRY_POINT = "no_entry_point"
ENTRY_POINT_INVALID = "entry_point_invalid"
IMPORT_FAILED = "import_failed"
SCHEMA_UNSUPPORTED = "schema_unsupported"
RETURN_ANNOTATION_MISSING = "return_annotation_missing"
# Codes of the issues `inspect` reports (documented in the README).
INSPECT_ERROR_CODES = (
    SYNTAX_ERROR,
    IMPORT_WRONG_MODULE,
    IMPORT_MISSING,
    NO_ENTRY_POINT,
    ENTRY_POINT_INVALID,
    IMPORT_FAILED,
    SCHEMA_UNSUPPORTED,
)

# One hint for every "the script is not set up as a routine script" problem.
SCRIPT_HINT = (
    "Add `from mobilerun_functions import Context, script` and decorate one function: "
    "`@script` `def main(input: Input, ctx: Context) -> Output:`"
)

# Modules newcomers import `script` from by mistake.
_WRONG_MODULES = frozenset(
    {"mobilerun", "mobilerun_routines", "mobilerun_sandbox", "mobilerun_sdk"}
)
_SDK_NAMES = ("script", "Context")


@dataclass(frozen=True)
class Issue:
    """One problem (or warning) found in a script."""

    code: str
    message: str
    line: int | None = None
    hint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", " ".join(self.message.split()))

    def as_json(self) -> dict[str, Any]:
        document: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.line is not None:
            document["line"] = self.line
        if self.hint is not None:
            document["repair"] = {"action": "fix_script", "hint": self.hint}
        return document

    def describe(self) -> str:
        """One line for stderr: `line N: message — hint`."""
        text = self.message if self.hint is None else f"{self.message} — {self.hint}"
        return text if self.line is None else f"line {self.line}: {text}"


def syntax_issue(exc: BaseException) -> Issue:
    """The issue for a script that does not compile (`SyntaxError`, or `ValueError`)."""
    line = exc.lineno if isinstance(exc, SyntaxError) else None
    message = exc.msg if isinstance(exc, SyntaxError) else str(exc)
    hint = None if line is None else f"Fix the syntax error on line {line}."
    return Issue(SYNTAX_ERROR, f"syntax error: {message}", line, hint)


def runtime_issue(code: str, message: str, exc: BaseException, path: str) -> Issue:
    """The issue for `exc`, raised while the script at `path` was imported.

    It has the line of the script frame in the traceback, and the repair hint when `exc`
    is one of the usual setup mistakes.
    """
    hint = SCRIPT_HINT if is_setup_mistake(exc) else None
    return Issue(code, message, _script_line(exc, path), hint)


def issues_for(
    exc: OSError | EntryPointError | ScriptImportError | SchemaError,
    source: bytes,
    path: str,
) -> list[Issue]:
    """Map load and contract failures to the same diagnostics for inspect and run."""
    if isinstance(exc, OSError):
        return [Issue(IMPORT_FAILED, f"cannot read script: {exc}")]
    if isinstance(exc, NoEntryPointError):
        return explain(Issue(NO_ENTRY_POINT, str(exc), hint=SCRIPT_HINT), source, path)
    if isinstance(exc, EntryPointError):
        issue = runtime_issue(ENTRY_POINT_INVALID, str(exc), exc, path)
        return explain(issue, source, path)
    if isinstance(exc, ScriptSyntaxError):
        return [syntax_issue(exc.original)]
    if isinstance(exc, ScriptImportError):
        message = f"{type(exc.original).__name__}: {exc.original}"
        issue = runtime_issue(IMPORT_FAILED, message, exc.original, path)
        return explain(issue, source, path) if issue.hint else [issue]
    return [Issue(SCHEMA_UNSUPPORTED, str(exc))]


def explain(failure: Issue, source: bytes | str, filename: str) -> list[Issue]:
    """`failure` (what the loader reported) together with what the source shows.

    A finding on the same line as `failure` explains it, so it takes its place.
    """
    try:
        tree = ast.parse(source, filename)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return [failure]
    found = sorted(_Scan(tree).run(), key=lambda issue: issue.line or 0)
    if failure.line is not None and any(issue.line == failure.line for issue in found):
        return found
    return [failure, *found]


class _Scan:
    """Imports of `script` from the wrong module, and `@script` without any binding."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.issues: list[Issue] = []
        self.script_bound = False  # something in the file binds the name `script`

    def run(self) -> list[Issue]:
        nodes = list(ast.walk(self.tree))
        for node in nodes:
            if isinstance(node, ast.ImportFrom):
                self._from_import(node)
            elif isinstance(node, ast.Import):
                self.script_bound |= any(_local(a) == "script" for a in node.names)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.script_bound |= node.name == "script"
            elif isinstance(node, ast.arg):
                self.script_bound |= node.arg == "script"
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                self.script_bound |= node.id == "script"
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._decorators(node)
        return self.issues

    def _from_import(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        root = module.split(".")[0]
        for alias in node.names:
            self.script_bound |= _local(alias) == "script" or (
                alias.name == "*" and root == PACKAGE
            )
            if node.level or root == PACKAGE:  # a sibling file, or the right module
                continue
            if alias.name == "script" or (alias.name == "Context" and root.startswith("mobilerun")):
                self.issues.append(
                    Issue(
                        IMPORT_WRONG_MODULE,
                        f"`{alias.name}` is not in `{module}`; it comes from `{PACKAGE}`",
                        node.lineno,
                        SCRIPT_HINT,
                    )
                )

    def _decorators(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Report the first `@script` (or `@script(...)`) that has nothing behind the name."""
        if self.script_bound or any(i.code == IMPORT_MISSING for i in self.issues):
            return
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Name) and target.id == "script":
                self.issues.append(
                    Issue(
                        IMPORT_MISSING,
                        "`script` is used but never imported",
                        target.lineno,
                        SCRIPT_HINT,
                    )
                )
                return


def _local(alias: ast.alias) -> str:
    return alias.asname or alias.name.split(".")[0]


def is_setup_mistake(exc: BaseException | None) -> bool:
    """A missing `script`/`Context` name or an import of the wrong SDK module, at the exception
    or its cause (the loader reports unresolved annotations with the `NameError` as cause)."""
    while exc is not None:
        name = getattr(exc, "name", None) or ""
        if isinstance(exc, NameError) and name in _SDK_NAMES:
            return True
        if isinstance(exc, ImportError) and name.split(".")[0] in _WRONG_MODULES:
            return True
        exc = exc.__cause__
    return False


def _script_line(exc: BaseException, path: str) -> int | None:
    """Line of the innermost traceback frame that belongs to the script file."""
    line = None
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == path:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line
