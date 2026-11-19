"""Safe loading of untrusted user strategy source code.

Two layers of defense:

1. ``scan_code`` performs a *static* AST walk that rejects obviously dangerous
   constructs BEFORE any code runs — dangerous imports (``os``, ``sys``,
   ``subprocess``, ``socket``, ``shutil``, ``ctypes``, ``multiprocessing``, ...),
   the ``__import__`` builtin, ``open``/``eval``/``exec``/``compile``, and any
   access to sandbox-escaping dunder attributes such as ``__globals__``,
   ``__builtins__``, ``__subclasses__`` or ``__class__``.
2. ``load_strategy`` executes the (scanned) source in a namespace whose
   ``__builtins__`` is a curated allowlist, then locates the concrete
   :class:`~quantdeck.strategies.sdk.Strategy` subclass, instantiates it, and
   attaches any user parameters.

IMPORTANT security note on where this runs. Static AST scanning is *necessary
but not sufficient* — a determined attacker can still defeat a pure in-process
allowlist. Therefore **live strategies run inside a subprocess sandbox**
(see :mod:`quantdeck.strategies.sandbox`) which additionally applies OS-level
resource limits and process isolation. Inside that child, ``load_strategy``
here does the scanning + restricted-builtins step; on its own (in-process) it
would be a weaker tradeoff and is not intended for hostile input.
"""
from __future__ import annotations

import ast
import math  # noqa: F401  (exposed to strategies via safe import allowlist)
from typing import Any, Optional

from ..core.errors import SandboxViolation, StrategyError
from .sdk import Strategy

# --------------------------------------------------------------------------- #
# Static AST policy                                                            #
# --------------------------------------------------------------------------- #

#: Top-level modules a strategy is allowed to import. Anything else is rejected.
#: ``quantdeck.strategies`` is allowed so users can ``from quantdeck.strategies
#: import Strategy``; nothing else under ``quantdeck`` is exposed.
#: NOTE: ``numpy``/``pandas`` are deliberately NOT allowed. Even though importing
#: them would pass the AST scan, their file-IO / (de)serialization surfaces are
#: ordinary library calls the scanner never sees and the in-process allowlist
#: cannot constrain: ``pd.read_pickle`` deserializes arbitrary pickles (arbitrary
#: code execution via ``__reduce__``), and ``pd.read_csv`` / ``DataFrame.to_csv``
#: / ``np.load`` / ``np.fromfile`` / ``ndarray.tofile`` grant arbitrary host
#: filesystem read AND write — all bypassing restricted builtins entirely. The
#: subprocess sandbox provides CPU/process isolation only (no filesystem
#: isolation, especially on macOS), so it cannot backstop this either. Do not
#: re-add these without real OS-level isolation (namespaces/seccomp/container).
ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        "math",
        "statistics",
        "datetime",
        "collections",
        "random",
        "itertools",
        "functools",
        "decimal",
        "quantdeck.strategies",
    }
)

#: Builtin/callable names that are hard-blocked when used as bare calls.
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "__import__",
        "open",
        "eval",
        "exec",
        "compile",
        "input",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "memoryview",
        "breakpoint",
    }
)

#: Attribute names whose mere access is a sandbox-escape vector.
FORBIDDEN_ATTRS: frozenset[str] = frozenset(
    {
        "__globals__",
        "__builtins__",
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__class__",
        "__dict__",
        "__code__",
        "__closure__",
        "__func__",
        "__self__",
        "__module__",
        "__init_subclass__",
        "__base__",
        "__reduce__",
        "__reduce_ex__",
        "__getattribute__",
    }
)

#: Modules whose import is always rejected even if they slipped past the
#: allowlist check (belt-and-suspenders: matches on the root package name).
BANNED_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "shutil",
        "pathlib",
        "ctypes",
        "multiprocessing",
        "threading",
        "asyncio",
        "importlib",
        "builtins",
        "pickle",
        "marshal",
        "shelve",
        "requests",
        "urllib",
        "http",
        "ftplib",
        "smtplib",
        "resource",
        "signal",
        "gc",
        "inspect",
        "code",
        "codeop",
        "pty",
        "tempfile",
    }
)


class _SandboxScanner(ast.NodeVisitor):
    """Walks the AST once and raises :class:`SandboxViolation` on any hit."""

    def _reject(self, node: ast.AST, what: str) -> None:
        line = getattr(node, "lineno", "?")
        raise SandboxViolation(f"Disallowed operation at line {line}: {what}")

    # --- Imports -------------------------------------------------------- #
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in BANNED_IMPORT_ROOTS:
                self._reject(node, f"import of banned module '{alias.name}'")
            if alias.name not in ALLOWED_IMPORTS and root not in ALLOWED_IMPORTS:
                self._reject(node, f"import of '{alias.name}' is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        root = module.split(".")[0]
        if node.level and node.level > 0:
            self._reject(node, "relative imports are not allowed")
        if root in BANNED_IMPORT_ROOTS:
            self._reject(node, f"import from banned module '{module}'")
        if module not in ALLOWED_IMPORTS and root not in ALLOWED_IMPORTS:
            self._reject(node, f"import from '{module}' is not allowed")
        self.generic_visit(node)

    # --- Dangerous calls / names --------------------------------------- #
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
            self._reject(node, f"call to forbidden builtin '{func.id}()'")
        # Block e.g. ``().__class__`` style escapes reached via attribute calls.
        if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_ATTRS:
            self._reject(node, f"call to forbidden attribute '{func.attr}'")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # A bare reference to a forbidden builtin (e.g. assigning ``e = eval``)
        # is just as dangerous as calling it.
        if node.id in FORBIDDEN_NAMES:
            self._reject(node, f"reference to forbidden name '{node.id}'")
        self.generic_visit(node)

    # --- Attribute-based escapes --------------------------------------- #
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRS:
            self._reject(node, f"access to forbidden attribute '{node.attr}'")
        # Reject any dunder attribute access we did not explicitly permit;
        # dunder access is the classic sandbox-escape lever.
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self._reject(node, f"dunder attribute access '{node.attr}'")
        self.generic_visit(node)


def scan_code(source: str) -> None:
    """Statically validate ``source``. Raise :class:`SandboxViolation` on any
    dangerous construct; return ``None`` if the code passes the policy.

    Parsing failures surface as :class:`StrategyError` so callers can tell a
    syntax error apart from a security rejection.
    """
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:  # pragma: no cover - trivial
        raise StrategyError(f"Strategy source has a syntax error: {exc}") from exc
    _SandboxScanner().visit(tree)


# --------------------------------------------------------------------------- #
# Restricted execution + strategy discovery                                    #
# --------------------------------------------------------------------------- #

#: The only builtins user code may see. Deliberately excludes ``__import__``,
#: ``open``, ``eval``, ``exec``, ``compile``, ``globals``, ``getattr`` etc.
_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "int", "isinstance", "issubclass", "len",
    "list", "map", "max", "min", "pow", "print", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "str", "sum", "tuple", "type", "zip",
    "True", "False", "None", "bytes", "bytearray", "complex", "hash", "hex",
    "oct", "bin", "chr", "ord", "abs", "iter", "next",
    # Exception types — needed for ordinary try/except/raise in user code.
    # Exposing exception classes is not a sandbox-escape lever.
    "Exception", "BaseException", "ValueError", "TypeError", "KeyError",
    "IndexError", "ZeroDivisionError", "ArithmeticError", "RuntimeError",
    "StopIteration", "AttributeError", "AssertionError", "OverflowError",
    "FloatingPointError", "NameError", "NotImplementedError", "LookupError",
)


def _safe_builtins() -> dict[str, Any]:
    """Build the curated ``__builtins__`` mapping handed to user code."""
    import builtins as _b

    allowed: dict[str, Any] = {}
    for nm in _SAFE_BUILTIN_NAMES:
        if hasattr(_b, nm):
            allowed[nm] = getattr(_b, nm)

    # A restricted importer so ``import math`` etc. work but banned modules do
    # not. This complements (does not replace) the static ``scan_code`` check.
    real_import = _b.__import__

    def _guarded_import(
        name: str,
        globals: Optional[dict] = None,
        locals: Optional[dict] = None,
        fromlist: tuple = (),
        level: int = 0,
    ):
        root = name.split(".")[0]
        if level != 0:
            raise SandboxViolation("relative imports are not allowed")
        if root in BANNED_IMPORT_ROOTS:
            raise SandboxViolation(f"import of '{name}' is not allowed")
        if name not in ALLOWED_IMPORTS and root not in ALLOWED_IMPORTS:
            raise SandboxViolation(f"import of '{name}' is not allowed")
        return real_import(name, globals, locals, fromlist, level)

    allowed["__import__"] = _guarded_import
    # ``__build_class__`` is required for the ``class`` statement to work under
    # a restricted ``__builtins__``. It is not itself a sandbox-escape lever
    # (the classes it builds still see only these safe builtins), so exposing it
    # is safe and necessary for user strategies to define their Strategy class.
    allowed["__build_class__"] = _b.__build_class__
    return allowed


def load_strategy(source: str, params: Optional[dict] = None) -> Strategy:
    """Scan, execute, and instantiate the concrete ``Strategy`` in ``source``.

    Returns a ready-to-run strategy instance with ``params`` attached (readable
    via ``ctx.param(...)`` at runtime). Raises :class:`SandboxViolation` if the
    code is unsafe, or :class:`StrategyError` if no ``Strategy`` subclass is
    found or instantiation fails.
    """
    scan_code(source)

    namespace: dict[str, Any] = {
        "__builtins__": _safe_builtins(),
        "__name__": "quantdeck_user_strategy",
    }
    # Pre-import the SDK so ``from quantdeck.strategies import Strategy`` binds
    # to our real base class (identity matters for the subclass search below).
    namespace["Strategy"] = Strategy

    try:
        compiled = compile(source, "<strategy>", "exec")
        exec(compiled, namespace)  # noqa: S102 - intentional, scanned + restricted
    except SandboxViolation:
        raise
    except Exception as exc:  # noqa: BLE001
        raise StrategyError(f"Failed to load strategy: {exc}") from exc

    strategy_cls = _find_strategy_class(namespace)
    if strategy_cls is None:
        raise StrategyError("No Strategy subclass found in the provided source.")

    try:
        instance = strategy_cls()
    except Exception as exc:  # noqa: BLE001
        raise StrategyError(f"Failed to instantiate strategy: {exc}") from exc

    # Attach params for ctx.param() to read; keep them on the instance so the
    # strategy context can look them up.
    instance.params = dict(params or {})  # type: ignore[attr-defined]
    return instance


def _find_strategy_class(namespace: dict[str, Any]) -> Optional[type[Strategy]]:
    """Return the single concrete ``Strategy`` subclass defined in ``namespace``.

    Prefers a class actually defined by the user (not the imported base class),
    and, when several exist, the most-derived one.
    """
    candidates: list[type[Strategy]] = []
    for value in namespace.values():
        if (
            isinstance(value, type)
            and issubclass(value, Strategy)
            and value is not Strategy
        ):
            candidates.append(value)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # Multiple: pick the most-derived (a leaf that nothing else subclasses).
    leaves = [
        c for c in candidates
        if not any(other is not c and issubclass(other, c) for other in candidates)
    ]
    return leaves[0] if leaves else candidates[-1]
