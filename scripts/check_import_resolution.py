#!/usr/bin/env python
"""Static import resolution checker.

This script walks the project tree looking for Python modules whose imports
cannot be resolved. It is intended to be used as a lightweight linting step
outside the runtime test suite.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple, TypeAlias

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = {
    "media",
    "static",
    "venv",
    ".venv",
    "env",
    "node_modules",
    "__pycache__",
    ".git",
}
OPTIONAL_MODULES = {
    "RPi.GPIO",
    "aiortc",
    "av",
    "build",
    "cv2",
    "gpiozero",
    "graphviz",
    "mfrc522",
    "numpy",
    "playwright",
    "plyer",
    "pwd",
    "pyftpdlib",
    "qrcode",
    "resource",
    "selenium",
    "smbus",
    "smbus2",
    "trio",
    "trio_websocket",
}
OPTIONAL_IMPORT_MARKER = "optional-import"
OPTIONAL_IMPORT_HELPERS = {"optional_import", "optional_import_block"}
PackageExports: TypeAlias = set[str]
PackageExportsCache: TypeAlias = dict[Path, PackageExports]


def _prepare_django() -> None:
    """Initialize Django settings when the project dependencies are available.

    Returns:
        None.
    """

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        "optional-import"
        import django  # type: ignore[import-not-found]
    except ImportError:
        return
    django.setup(set_prefix=False)


class ImportIssue(NamedTuple):
    """Structured details for a single unresolved import reference."""

    module: str
    path: Path
    lineno: int
    message: str


def iter_python_files(root: Path) -> Iterator[Path]:
    """Yield Python files beneath ``root`` while skipping ignored directories.

    Args:
        root: Directory tree to scan.

    Returns:
        An iterator of project Python file paths.
    """

    for path in root.rglob("*.py"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        yield path


def module_path_from_file(path: Path) -> str | None:
    """Convert a project file path into a dotted Python module path.

    Args:
        path: Absolute or project-relative Python file path.

    Returns:
        Dotted import path relative to ``PROJECT_ROOT``, or ``None`` when the
        file does not live under the repository root.
    """

    try:
        relative = path.relative_to(PROJECT_ROOT)
    except ValueError:
        return None
    return ".".join(relative.with_suffix("").parts)


def resolve_import(module: str, package: str | None, level: int) -> str | None:
    """Resolve a possibly-relative import target to an absolute module name.

    Args:
        module: Imported module or symbol name.
        package: Package context used for relative imports.
        level: Relative import level from the AST node.

    Returns:
        The resolved absolute module name, or ``None`` when resolution fails.
    """

    if level:
        if package is None:
            return None
        try:
            return importlib.util.resolve_name("." * level + (module or ""), package)
        except ImportError:
            return None
    return module


def is_optional_module(module: str) -> bool:
    """Return ``True`` when ``module`` is an optional module path."""

    prefix = ""
    for part in module.split("."):
        prefix = f"{prefix}.{part}" if prefix else part
        if prefix in OPTIONAL_MODULES:
            return True
    return False


def is_missing_optional_module(module: str) -> bool:
    """Return ``True`` when an optional module is not importable in runtime."""

    if not is_optional_module(module):
        return False
    top_level_module = module.split(".", 1)[0]
    try:
        return importlib.util.find_spec(top_level_module) is None
    except (ImportError, ModuleNotFoundError, ValueError):
        return True


class ImportCollector(ast.NodeVisitor):
    """Collect unresolved imports from a parsed Python module."""

    def __init__(self, file_path: Path, package: str | None):
        """Initialize collector state for a single file.

        Args:
            file_path: Python file being inspected.
            package: Dotted package name for relative import resolution.
        """

        self.file_path = file_path
        self.package = package
        self.type_checking_stack: list[bool] = []
        self.optional_import_stack: list[bool] = []
        self.issues: list[ImportIssue] = []
        self._package_exports_cache: PackageExportsCache = {}

    def visit_If(self, node: ast.If) -> None:
        """Track ``TYPE_CHECKING`` guards while traversing child nodes."""

        condition = self._is_type_checking(node.test)
        self.type_checking_stack.append(condition)
        self.generic_visit(node)
        self.type_checking_stack.pop()

    def visit_Try(self, node: ast.Try) -> None:
        """Track optional-import guards while traversing child nodes."""

        optional = self._is_explicit_optional_try(
            node
        ) or self._is_legacy_guarded_optional_try(node)
        self.optional_import_stack.append(optional)
        self.generic_visit(node)
        self.optional_import_stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        """Validate absolute imports in regular execution paths."""

        if self._skip_node():
            return
        for alias in node.names:
            self._check_import(alias.name, node.lineno, level=0)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Validate ``from ... import ...`` statements in regular execution paths."""

        if self._skip_node():
            return
        if node.level:
            self._check_relative_import(node)
            return
        base_module = node.module or ""
        if base_module:
            self._check_import(base_module, node.lineno, level=node.level)
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            self._check_import(alias.name, node.lineno, level=node.level)

    def _check_import(self, module: str, lineno: int, level: int) -> None:
        """Record unresolved imports for absolute import statements.

        Args:
            module: Imported module name.
            lineno: Source line number for the import.
            level: Relative import level, kept for call-site symmetry.

        Returns:
            None.
        """

        if not module:
            return
        resolved_module = resolve_import(module, self.package, level)
        if resolved_module is None:
            return
        if is_missing_optional_module(resolved_module):
            return
        module_path = PROJECT_ROOT / Path(resolved_module.replace(".", "/"))
        if self._path_exists(module_path):
            return
        try:
            spec = importlib.util.find_spec(resolved_module)
        except Exception:
            spec = None
        if spec is None:
            self.issues.append(
                ImportIssue(
                    resolved_module,
                    self.file_path,
                    lineno,
                    "import could not be resolved",
                )
            )

    def _check_relative_import(self, node: ast.ImportFrom) -> None:
        """Record unresolved imports for relative import statements.

        Args:
            node: ``from ... import ...`` AST node using a relative level.

        Returns:
            None.
        """

        target_dir = self.file_path.parent
        for _ in range(max(node.level - 1, 0)):
            target_dir = target_dir.parent

        if node.module:
            module_path = target_dir / Path(node.module.replace(".", "/"))
            if not self._path_exists(module_path):
                alt_module_path = target_dir.parent / Path(
                    node.module.replace(".", "/")
                )
                if not self._path_exists(alt_module_path):
                    self.issues.append(
                        ImportIssue(
                            node.module,
                            self.file_path,
                            node.lineno,
                            "unable to resolve relative import",
                        )
                    )
            return

        for alias in node.names:
            if alias.name == "*":
                continue
            init_exports = self._package_exports(target_dir)
            if alias.name in init_exports:
                continue
            module_path = target_dir / Path(alias.name.replace(".", "/"))
            if not self._path_exists(module_path):
                alt_module_path = target_dir.parent / Path(alias.name.replace(".", "/"))
                if not self._path_exists(alt_module_path):
                    self.issues.append(
                        ImportIssue(
                            alias.name,
                            self.file_path,
                            node.lineno,
                            "unable to resolve relative import",
                        )
                    )

    def _package_exports(self, package_dir: Path) -> PackageExports:
        """Return importable names exposed by a package ``__init__`` module.

        Args:
            package_dir: Package directory containing an ``__init__.py`` file.

        Returns:
            Exported names inferred from assignments, imports, and ``__all__``.
        """

        cached = self._package_exports_cache.get(package_dir)
        if cached is not None:
            return cached

        init_path = package_dir / "__init__.py"
        if not init_path.exists():
            self._package_exports_cache[package_dir] = set()
            return set()

        try:
            tree = ast.parse(init_path.read_text(encoding="utf-8"))
        except Exception:
            self._package_exports_cache[package_dir] = set()
            return set()

        exported_names: PackageExports = set()
        explicit_all: PackageExports | None = None

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                exported_names.add(node.name)
                continue
            if isinstance(node, ast.Import):
                for import_alias in node.names:
                    exported_names.add(
                        import_alias.asname or import_alias.name.split(".")[-1]
                    )
                continue
            if isinstance(node, ast.ImportFrom):
                for import_alias in node.names:
                    if import_alias.name == "*":
                        continue
                    exported_names.add(import_alias.asname or import_alias.name)
                continue
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        exported_names.add(target.id)
                        if target.id == "__all__":
                            explicit_all = self._extract_all_names(node.value)

        result = explicit_all if explicit_all is not None else exported_names
        self._package_exports_cache[package_dir] = result
        return result

    @staticmethod
    def _extract_all_names(node: ast.expr) -> PackageExports:
        """Extract string literal names from a ``__all__`` assignment value."""

        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return set()
        values: PackageExports = set()
        for element in node.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                values.add(element.value)
        return values

    @staticmethod
    def _path_exists(path: Path) -> bool:
        """Return ``True`` when a module path resolves to a file or package."""

        return path.with_suffix(".py").exists() or (path / "__init__.py").exists()

    def _skip_node(self) -> bool:
        """Return ``True`` when an import should be ignored for this traversal path."""

        return any(self.type_checking_stack) or any(self.optional_import_stack)

    def _is_explicit_optional_try(self, node: ast.Try) -> bool:
        """Return ``True`` when a ``try`` block explicitly marks optional imports."""

        has_import_error_handler = any(
            self._is_import_error_handler(handler) for handler in node.handlers
        )
        if not has_import_error_handler:
            return False
        return any(
            self._is_optional_marker_statement(statement) for statement in node.body
        )

    def _is_legacy_guarded_optional_try(self, node: ast.Try) -> bool:
        """Allow legacy ``ImportError`` guards that explicitly re-raise failures.

        During transition to marker-based opt-ins, existing patterns that catch
        ``ImportError`` and immediately raise a domain-specific error should
        continue to be treated as optional import guards.
        """

        return any(
            self._is_import_error_handler(handler)
            and any(isinstance(statement, ast.Raise) for statement in handler.body)
            for handler in node.handlers
        )

    @staticmethod
    def _is_optional_marker_statement(statement: ast.stmt) -> bool:
        """Return ``True`` if a statement marks the enclosing ``try`` as optional."""

        if not isinstance(statement, ast.Expr):
            return False

        if isinstance(statement.value, ast.Constant) and isinstance(
            statement.value.value, str
        ):
            return OPTIONAL_IMPORT_MARKER in statement.value.value

        if not isinstance(statement.value, ast.Call):
            return False

        func = statement.value.func
        if isinstance(func, ast.Name):
            return func.id in OPTIONAL_IMPORT_HELPERS
        return False

    @staticmethod
    def _is_type_checking(node: ast.expr) -> bool:
        """Return ``True`` for ``TYPE_CHECKING`` guard expressions."""

        return (isinstance(node, ast.Name) and node.id == "TYPE_CHECKING") or (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "typing"
            and node.attr == "TYPE_CHECKING"
        )

    @staticmethod
    def _is_import_error_handler(handler: ast.excepthandler) -> bool:
        """Return ``True`` when an exception handler catches ``ImportError``."""

        if not isinstance(handler, ast.ExceptHandler):
            return False
        import_error_names = {"ImportError", "ModuleNotFoundError"}
        if isinstance(handler.type, ast.Name):
            return handler.type.id in import_error_names
        if isinstance(handler.type, ast.Tuple):
            return any(
                isinstance(elt, ast.Name) and elt.id in import_error_names
                for elt in handler.type.elts
            )
        return False


def collect_missing_imports(files: Iterable[Path]) -> list[ImportIssue]:
    """Collect unresolved imports from the given Python files.

    Args:
        files: Python files to inspect.

    Returns:
        Sorted import issues are not guaranteed; callers may sort later.
    """

    issues: list[ImportIssue] = []
    for file_path in files:
        module_path = module_path_from_file(file_path)
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        collector = ImportCollector(
            file_path, module_path.rpartition(".")[0] or None if module_path else None
        )
        collector.visit(tree)
        issues.extend(collector.issues)
    return issues


def _is_in_ignored_dir(path: Path) -> bool:
    """Return True when a project-relative path lives in an ignored directory."""

    return any(part in IGNORED_DIRS for part in path.parts)


def _collect_target_files(paths: list[str]) -> list[Path]:
    """Resolve CLI-provided paths into Python files under project root.

    Args:
        paths: Optional file or directory paths supplied on the command line.

    Returns:
        Deduplicated Python files under ``PROJECT_ROOT``.
    """

    if not paths:
        return list(iter_python_files(PROJECT_ROOT))

    files: set[Path] = set()
    for raw_path in paths:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        else:
            candidate = candidate.resolve()

        if not candidate.exists():
            continue

        if candidate.is_file():
            if candidate.suffix == ".py" and PROJECT_ROOT in candidate.parents:
                relative_candidate = candidate.relative_to(PROJECT_ROOT)
                if not _is_in_ignored_dir(relative_candidate):
                    files.add(candidate)
            continue

        for file_path in iter_python_files(candidate):
            if PROJECT_ROOT in file_path.parents:
                files.add(file_path)

    return sorted(files)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for import resolution checks.

    Args:
        argv: Optional CLI argument list.

    Returns:
        Parsed command-line namespace.
    """

    parser = argparse.ArgumentParser(
        description="Check Python imports resolve correctly."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional files or directories to check. Defaults to the whole repository.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the import resolution checker as a command-line tool.

    Args:
        argv: Optional CLI argument list.

    Returns:
        Process exit code, where ``0`` means all imports resolved.
    """

    sys.path.insert(0, str(PROJECT_ROOT))
    _prepare_django()
    args = parse_args(argv)
    files = _collect_target_files(args.paths)
    issues = collect_missing_imports(files)
    if issues:
        formatted = "\n".join(
            f"{issue.path.relative_to(PROJECT_ROOT)}:{issue.lineno} -> {issue.module} ({issue.message})"
            for issue in sorted(issues)
        )
        print(f"Unresolved imports detected:\n{formatted}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
