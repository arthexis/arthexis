#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

DOCUMENT_SUFFIXES = {".md", ".rst", ".txt"}
SOURCE_SUFFIXES = {
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".py",
    ".sh",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
SOURCE_ROOTS = {".github", "apps", "scripts", "tests", "utils"}
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "htmlcov",
    "node_modules",
    "staticfiles",
    "work",
}
SPANISH_RE = re.compile(
    r"[áéíóúñü¿¡]|"
    r"\b(el|la|los|las|un|una|unos|unas|para|con|sin|que|de|por|"
    r"operador|operadora|documentación|configuración|versión|política)\b",
    re.IGNORECASE,
)
ENGLISH_RE = re.compile(
    r"\b(the|and|with|without|should|must|release|operator|configuration|"
    r"documentation|policy|version|workflow|issue|pull request|return|whether|"
    r"register|feature|active|default|helper|service|upgrade|unit|command|"
    r"required|start|admin|preview|update|rendered|upload|value|values|"
    r"energy|power|infrastructure)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    surface: str
    classification: str
    status: str
    has_english: bool
    has_es_mx: bool


@dataclass(frozen=True)
class InventoryReport:
    entries: list[InventoryEntry]

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {
            "total": len(self.entries),
            "english": 0,
            "missing_english": 0,
            "source_adjacent_needs_english_review": 0,
            "preserve": 0,
            "needs_review": 0,
        }
        for entry in self.entries:
            key = entry.status.replace("-", "_")
            counts[key] = counts.get(key, 0) + 1
        return counts

    @property
    def blocking_entries(self) -> list[InventoryEntry]:
        blocking_statuses = {
            "missing-english",
            "source-adjacent-needs-english-review",
            "needs-review",
        }
        return [entry for entry in self.entries if entry.status in blocking_statuses]


def _is_readme(path: Path) -> bool:
    return path.suffix.lower() in DOCUMENT_SUFFIXES and path.name.lower().startswith("readme")


def _is_document(path: Path) -> bool:
    return path.suffix.lower() in DOCUMENT_SUFFIXES and (
        _is_readme(path) or path.parts[:1] == ("docs",)
    )


def _is_source_adjacent(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_SUFFIXES and bool(path.parts) and path.parts[0] in SOURCE_ROOTS


def _is_preserved_source_adjacent(path: Path) -> bool:
    parts = set(path.parts)
    if "fixtures" in parts:
        return True
    if path.suffix.lower() == ".json" and len(path.stem) == 2:
        return True
    return "static" in parts and "htmx" in parts


def _is_within_repo(path: Path, repo_root: Path) -> bool:
    try:
        path.relative_to(repo_root)
    except ValueError:
        return False
    return True


def _iter_candidate_paths(repo_root: Path) -> list[Path]:
    repo_root = repo_root.resolve()
    candidates: list[Path] = []
    for path in repo_root.rglob("*"):
        relative = path.relative_to(repo_root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if not path.is_file():
            continue
        try:
            resolved_path = path.resolve(strict=True)
        except OSError:
            continue
        if not _is_within_repo(resolved_path, repo_root):
            continue
        if _is_document(relative) or _is_source_adjacent(relative):
            candidates.append(relative)
    return sorted(candidates)


def classify_text(path: Path, text: str) -> InventoryEntry:
    has_es_mx = bool(SPANISH_RE.search(text))
    has_english = bool(ENGLISH_RE.search(text))

    if _is_document(path):
        classification = "english-doc"
        if has_english:
            status = "english"
        elif has_es_mx:
            status = "missing-english"
        else:
            status = "needs-review"
        surface = "README/docs"
    elif _is_source_adjacent(path):
        classification = "source-adjacent-prose"
        if _is_preserved_source_adjacent(path):
            status = "preserve"
        elif has_english:
            status = "english"
        elif has_es_mx:
            status = "source-adjacent-needs-english-review"
        else:
            status = "preserve"
        surface = "source-adjacent"
    else:
        classification = "preserve"
        status = "preserve"
        surface = "other"

    return InventoryEntry(
        path=path.as_posix(),
        surface=surface,
        classification=classification,
        status=status,
        has_english=has_english,
        has_es_mx=has_es_mx,
    )


def build_inventory(repo_root: Path) -> InventoryReport:
    entries = []
    for path in _iter_candidate_paths(repo_root):
        text = (repo_root / path).read_text(encoding="utf-8", errors="ignore")
        entries.append(classify_text(path, text))
    return InventoryReport(entries=entries)


def format_markdown(report: InventoryReport, *, limit: int) -> str:
    summary = report.summary
    lines = [
        "# Language Policy Inventory",
        "",
        f"- Total surfaces: {summary['total']}",
        f"- English-covered surfaces: {summary['english']}",
        f"- README/docs missing English: {summary['missing_english']}",
        "- Source-adjacent prose needing English review: "
        f"{summary['source_adjacent_needs_english_review']}",
        "",
        "## Blocking or Review Items",
    ]
    blocking_entries = report.blocking_entries
    if not blocking_entries:
        lines.append("- None detected.")
    else:
        for entry in blocking_entries[:limit]:
            lines.append(f"- `{entry.path}`: {entry.status}")
        remaining = len(blocking_entries) - limit
        if remaining > 0:
            lines.append(f"- ... and {remaining} more")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory Arthexis README/docs and source-adjacent prose for English coverage under the 1.0.0 language policy.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to scan.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=40,
        help="Maximum blocking/review entries to print in markdown output.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when non-exempt policy gaps are detected.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_inventory(args.repo_root)

    if args.format == "json":
        payload = {
            "summary": report.summary,
            "entries": [asdict(entry) for entry in report.entries],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_markdown(report, limit=args.limit))

    return 1 if args.strict and report.blocking_entries else 0


if __name__ == "__main__":
    sys.exit(main())
