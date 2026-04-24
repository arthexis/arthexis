#!/usr/bin/env python3
"""Validate admin template class naming and styling contract."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = REPO_ROOT / "docs/development/ui-style-index.md"
TEMPLATE_GLOB = "apps/*/templates/admin/**/*.html"
WAIVER_PATTERN = re.compile(r"ui-style-contract:\s*waive-new-class\s+([A-Za-z_][A-Za-z0-9_-]*)")
CLASS_ATTR_PATTERN = re.compile(r"class\s*=\s*([\"'])(.*?)\1", re.DOTALL)
CSS_CLASS_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])\.([A-Za-z_][A-Za-z0-9_-]*)")
STATIC_CSS_PATTERN = re.compile(r"(?:\{\%\s*static\s+[\"']([^\"']+\.css)[\"']\s*\%\}|href\s*=\s*[\"']([^\"']+\.css)[\"'])")
VALID_CLASS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
INDEX_CLASS_PATTERN = re.compile(r"^-\s+`([A-Za-z_][A-Za-z0-9_-]*)`\s*$")
ALLOW_CUSTOM_CSS_MARKER = "admin-ui-framework: allow-custom-css"
FORBIDDEN_GENERIC_CLASS_NAMES = {
    "box",
    "container",
    "footer",
    "header",
    "item",
    "title",
    "wrapper",
}


@dataclass(frozen=True)
class Violation:
    """A single contract violation reported by the checker."""

    code: str
    path: Path
    line: int
    message: str


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _normalize_class_name(token: str) -> str | None:
    if "{" in token or "}" in token or "%" in token:
        return None
    token = token.strip()
    if not token:
        return None
    if not VALID_CLASS_PATTERN.match(token):
        return None
    return token


def _iter_template_class_names(path: Path) -> Iterable[tuple[str, int]]:
    content = path.read_text(encoding="utf-8")
    for match in CLASS_ATTR_PATTERN.finditer(content):
        for token in re.split(r"\s+", match.group(2).strip()):
            class_name = _normalize_class_name(token)
            if not class_name:
                continue
            yield class_name, _line_number(content, match.start())


def _iter_css_class_names(path: Path) -> Iterable[tuple[str, int]]:
    content = path.read_text(encoding="utf-8")
    for match in CSS_CLASS_PATTERN.finditer(content):
        yield match.group(1), _line_number(content, match.start())


def _class_prefix(class_name: str) -> str:
    if class_name.startswith("admin-ui-"):
        return "admin-ui"
    if "-" in class_name:
        return class_name.split("-", 1)[0]
    if "_" in class_name:
        return class_name.split("_", 1)[0]
    return ""


def _read_indexed_classes(index_path: Path) -> set[str]:
    indexed_classes: set[str] = set()
    if not index_path.exists():
        return indexed_classes
    for line in index_path.read_text(encoding="utf-8").splitlines():
        match = INDEX_CLASS_PATTERN.match(line.strip())
        if match:
            indexed_classes.add(match.group(1))
    return indexed_classes


def _read_waivers(path: Path) -> set[str]:
    content = path.read_text(encoding="utf-8")
    return {match.group(1) for match in WAIVER_PATTERN.finditer(content)}


def _collect_templates(root: Path) -> list[Path]:
    return sorted(root.glob(TEMPLATE_GLOB))


def _resolve_css_candidates(root: Path, css_ref: str) -> list[Path]:
    normalized = css_ref.lstrip("/")
    candidates: list[Path] = [root / normalized]
    candidates.extend(root.glob(f"apps/*/static/{normalized}"))
    return [candidate for candidate in candidates if candidate.exists()]


def _collect_related_stylesheets(root: Path, template_paths: list[Path]) -> list[Path]:
    stylesheet_paths: set[Path] = set()
    for template_path in template_paths:
        content = template_path.read_text(encoding="utf-8")
        for match in STATIC_CSS_PATTERN.finditer(content):
            css_ref = match.group(1) or match.group(2)
            if not css_ref:
                continue
            stylesheet_paths.update(_resolve_css_candidates(root, css_ref))
    return sorted(stylesheet_paths)


def _validate_inline_styles(template_path: Path) -> list[Violation]:
    content = template_path.read_text(encoding="utf-8")
    if ALLOW_CUSTOM_CSS_MARKER in content:
        return []

    violations: list[Violation] = []
    for match in re.finditer(r"<style\b", content):
        violations.append(
            Violation(
                code="INLINE_STYLE_DISALLOWED",
                path=template_path,
                line=_line_number(content, match.start()),
                message=(
                    "Inline <style> block requires marker 'admin-ui-framework: allow-custom-css'."
                ),
            )
        )
    for match in re.finditer(r"\sstyle\s*=", content):
        violations.append(
            Violation(
                code="INLINE_STYLE_DISALLOWED",
                path=template_path,
                line=_line_number(content, match.start()),
                message=(
                    "Inline style attribute requires marker 'admin-ui-framework: allow-custom-css'."
                ),
            )
        )
    return violations


def run_check(repo_root: Path = REPO_ROOT, index_path: Path = INDEX_PATH) -> list[Violation]:
    """Run the UI style contract check and return all violations."""

    template_paths = _collect_templates(repo_root)
    stylesheet_paths = _collect_related_stylesheets(repo_root, template_paths)
    indexed_classes = _read_indexed_classes(index_path)
    known_prefixes = {_class_prefix(class_name) for class_name in indexed_classes if _class_prefix(class_name)}

    violations: list[Violation] = []
    if not indexed_classes:
        violations.append(
            Violation(
                code="MISSING_STYLE_INDEX",
                path=index_path,
                line=1,
                message="ui-style-index.md is missing or does not contain any class entries.",
            )
        )

    for template_path in template_paths:
        waivers = _read_waivers(template_path)
        violations.extend(_validate_inline_styles(template_path))
        for class_name, line_number in _iter_template_class_names(template_path):
            violations.extend(
                _validate_class_name(
                    class_name=class_name,
                    line_number=line_number,
                    path=template_path,
                    indexed_classes=indexed_classes,
                    known_prefixes=known_prefixes,
                    waivers=waivers,
                )
            )

    for stylesheet_path in stylesheet_paths:
        waivers = _read_waivers(stylesheet_path)
        for class_name, line_number in _iter_css_class_names(stylesheet_path):
            violations.extend(
                _validate_class_name(
                    class_name=class_name,
                    line_number=line_number,
                    path=stylesheet_path,
                    indexed_classes=indexed_classes,
                    known_prefixes=known_prefixes,
                    waivers=waivers,
                )
            )

    return sorted(violations, key=lambda item: (str(item.path), item.line, item.code, item.message))


def _validate_class_name(
    *,
    class_name: str,
    line_number: int,
    path: Path,
    indexed_classes: set[str],
    known_prefixes: set[str],
    waivers: set[str],
) -> list[Violation]:
    violations: list[Violation] = []

    if class_name in FORBIDDEN_GENERIC_CLASS_NAMES:
        violations.append(
            Violation(
                code="FORBIDDEN_GENERIC_CLASS",
                path=path,
                line=line_number,
                message=f"Forbidden generic class name '{class_name}'.",
            )
        )

    if class_name in indexed_classes or class_name in waivers:
        return violations

    prefix = _class_prefix(class_name)
    if prefix and known_prefixes and prefix not in known_prefixes:
        violations.append(
            Violation(
                code="UNKNOWN_PREFIX",
                path=path,
                line=line_number,
                message=(
                    f"Class '{class_name}' uses unknown prefix '{prefix}'. Add it to ui-style-index.md or rename it."
                ),
            )
        )

    violations.append(
        Violation(
            code="NEW_CLASS_NOT_INDEXED",
            path=path,
            line=line_number,
            message=(
                f"Class '{class_name}' is not indexed. Add it to ui-style-index.md or waive temporarily "
                f"with 'ui-style-contract: waive-new-class {class_name}'."
            ),
        )
    )
    return violations


def _format_violation(violation: Violation, repo_root: Path) -> str:
    relative_path = violation.path.relative_to(repo_root)
    return f"{violation.code}: {relative_path}:{violation.line}: {violation.message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Repository root path.")
    parser.add_argument(
        "--index-path",
        default=str(INDEX_PATH),
        help="Path to ui-style-index.md file.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    index_path = Path(args.index_path).resolve()
    violations = run_check(repo_root=repo_root, index_path=index_path)
    if not violations:
        print("UI style contract check passed.")
        return 0

    print("UI style contract violations detected:")
    for violation in violations:
        print(f" - {_format_violation(violation, repo_root)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
