#!/usr/bin/env python
"""Static import resolution checker.

This script walks the project tree looking for Python modules whose imports
cannot be resolved. It is intended to be used as a lightweight linting step
outside the runtime test suite.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = {"media", "static", "venv", ".venv", "env", "node_modules", "__pycache__", ".git"}
OPTIONAL_MODULES = {
    "plyer",
    "smbus",
    "build",
    "RPi.GPIO",
    "mfrc522",
    "pwd",
    "resource",
}


def _prepare_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        import django
    except ImportError:
        return
    django.setup(set_prefix=False)


class ImportIssue(NamedTuple):
    module: str
    path: Path
    lineno: int
    message: str


def iter_python_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.py"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        yield path


def module_path_from_file(path: Path) -> str | None:
    try:
        relative = path.relative_to(PROJECT_ROOT)
    except ValueError:
        return None
    return ".".join(relative.with_suffix("").parts)


def resolve_import(module: str, package: str | None, level: int) -> str | None:
    if level:
        if package is None:
            return None
        try:
            return importlib.util.resolve_name("." * level + (module or ""), package)
        except ImportError:
            return None
    return module


class ImportCollector(ast.NodeVisitor):
    def __init__(self, file_path: Path, package: str | None):
        self.file_path = file_path
        self.package = package
        self.type_checking_stack: list[bool] = []
        self.optional_import_stack: list[bool] = []
        self.issues: list[ImportIssue] = []

    def visit_If(self, node: ast.If) -> None:
        condition = self._is_type_checking(node.test)
        self.type_checking_stack.append(condition)
        self.generic_visit(node)
        self.type_checking_stack.pop()

    def visit_Try(self, node: ast.Try) -> None:
        optional = any(self._is_import_error_handler(handler) for handler in node.handlers)
        self.optional_import_stack.append(optional)
        self.generic_visit(node)
        self.optional_import_stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        if self._skip_node():
            return
        for alias in node.names:
            self._check_import(alias.name, node.lineno, level=0)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
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
        if not module:
            return
        if module in OPTIONAL_MODULES:
            return
        module_path = PROJECT_ROOT / Path(module.replace(".", "/"))
        if self._path_exists(module_path):
            return
        try:
            spec = importlib.util.find_spec(module)
        except Exception:
            spec = None
        if spec is None:
            self.issues.append(
                ImportIssue(module, self.file_path, lineno, "import could not be resolved")
            )

    def _check_relative_import(self, node: ast.ImportFrom) -> None:
        target_dir = self.file_path.parent
        for _ in range(max(node.level - 1, 0)):
            target_dir = target_dir.parent

        if node.module:
            module_path = target_dir / Path(node.module.replace(".", "/"))
            if not self._path_exists(module_path):
                alt_module_path = target_dir.parent / Path(node.module.replace(".", "/"))
                if not self._path_exists(alt_module_path):
                    self.issues.append(
                        ImportIssue(node.module, self.file_path, node.lineno, "unable to resolve relative import")
                    )
            return

        for alias in node.names:
            if alias.name == "*":
                continue
            module_path = target_dir / Path(alias.name.replace(".", "/"))
            if not self._path_exists(module_path):
                alt_module_path = target_dir.parent / Path(alias.name.replace(".", "/"))
                if not self._path_exists(alt_module_path):
                    self.issues.append(
                        ImportIssue(alias.name, self.file_path, node.lineno, "unable to resolve relative import")
                    )

    @staticmethod
    def _path_exists(path: Path) -> bool:
        return path.with_suffix(".py").exists() or (path / "__init__.py").exists()

    def _skip_node(self) -> bool:
        return any(self.type_checking_stack) or any(self.optional_import_stack)

    @staticmethod
    def _is_type_checking(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Name) and node.id == "TYPE_CHECKING"
        ) or (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "typing"
            and node.attr == "TYPE_CHECKING"
        )

    @staticmethod
    def _is_import_error_handler(handler: ast.excepthandler) -> bool:
        if isinstance(handler.type, ast.Name):
            return handler.type.id == "ImportError"
        if isinstance(handler.type, ast.Tuple):
            return any(isinstance(elt, ast.Name) and elt.id == "ImportError" for elt in handler.type.elts)
        return False


def collect_missing_imports(files: Iterable[Path]) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for file_path in files:
        module_path = module_path_from_file(file_path)
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        collector = ImportCollector(file_path, module_path.rpartition(".")[0] or None if module_path else None)
        collector.visit(tree)
        issues.extend(collector.issues)
    return issues


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))
    _prepare_django()
    files = list(iter_python_files(PROJECT_ROOT))
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
