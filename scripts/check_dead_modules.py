#!/usr/bin/env python3
from __future__ import annotations

import ast
import pathlib
import sys


MINIMAL_PLACEHOLDER_PARTS = {
    "beat_migrations",
    "fixtures",
    "migrations",
    "node_modules",
}


def _is_ellipsis_expression(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and node.value.value is Ellipsis
    )


def _is_docstring_expression(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_dead_module(tree: ast.Module) -> bool:
    if not tree.body:
        return True

    for index, node in enumerate(tree.body):
        if index == 0 and _is_docstring_expression(node):
            continue
        if isinstance(node, ast.Pass):
            continue
        if _is_ellipsis_expression(node):
            continue
        return False

    return True


def _should_skip(path: pathlib.Path) -> bool:
    if path.name == "__init__.py":
        return True

    parts = set(path.parts)
    if MINIMAL_PLACEHOLDER_PARTS.intersection(parts):
        return True

    return False


def _iter_repo_python_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in repo_root.rglob("*.py")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(repo_root).parts)
        and not _should_skip(path.relative_to(repo_root))
    )


def _iter_input_paths(argv: list[str], repo_root: pathlib.Path) -> list[pathlib.Path]:
    if not argv:
        return _iter_repo_python_files(repo_root)

    paths = []
    for raw_path in argv:
        path = pathlib.Path(raw_path)
        if not path.is_absolute():
            path = (pathlib.Path.cwd() / path).resolve()
        if not path.is_file() or path.suffix != ".py":
            continue

        try:
            relative_path = path.relative_to(repo_root)
        except ValueError:
            continue

        if _should_skip(relative_path):
            continue

        paths.append(path)

    return sorted(set(paths))


def _collect_dead_modules(paths: list[pathlib.Path], repo_root: pathlib.Path) -> list[pathlib.Path]:
    dead_modules: list[pathlib.Path] = []

    for path in paths:
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        if _is_dead_module(tree):
            dead_modules.append(path.relative_to(repo_root))

    return dead_modules


def main(argv: list[str]) -> int:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    paths = _iter_input_paths(argv, repo_root)
    dead_modules = _collect_dead_modules(paths, repo_root)

    if dead_modules:
        print("Dead Python modules detected:")
        for path in dead_modules:
            print(f" - {path} (remove file or add implementation)")
        return 1

    print("No dead Python modules detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
