"""Utilities for working with coverage reports and SVG badges."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    """Aggregate information extracted from a coverage report."""

    covered_lines: int
    missing_lines: int
    num_statements: int

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CoverageSummary":
        """Build a summary from the JSON payload emitted by ``coverage json``."""

        totals = payload.get("totals")
        if not isinstance(totals, Mapping):
            raise ValueError("Coverage payload does not include totals data")

        try:
            covered = int(totals["covered_lines"])
            statements = int(totals["num_statements"])
        except KeyError as exc:  # pragma: no cover - defensive
            raise ValueError("Coverage totals are incomplete") from exc

        missing_raw = totals.get("missing_lines")
        if missing_raw is None:
            missing = max(statements - covered, 0)
        else:
            missing = max(int(missing_raw), 0)

        if statements < 0 or covered < 0:
            raise ValueError("Coverage totals cannot be negative")

        return cls(covered_lines=covered, missing_lines=missing, num_statements=statements)

    @property
    def percent(self) -> float:
        """Return the percentage of statements that were executed."""

        if self.num_statements <= 0:
            return 100.0 if self.missing_lines <= 0 else 0.0
        return (self.covered_lines / self.num_statements) * 100

    def to_dict(self) -> dict[str, float | int]:
        """Return a serialisable representation of the summary."""

        return {
            "covered_lines": self.covered_lines,
            "missing_lines": self.missing_lines,
            "num_statements": self.num_statements,
            "percent_covered": round(self.percent, 2),
        }


def load_summary(path: str | Path) -> CoverageSummary:
    """Load a :class:`CoverageSummary` from ``path``."""

    candidate = Path(path)
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - handled by caller
        raise exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Coverage report {candidate} is not valid JSON: {exc}") from exc
    return CoverageSummary.from_payload(payload)


def coverage_color(percentage: float) -> str:
    """Return a Shields-style colour for ``percentage``."""

    if percentage >= 90:
        return "#4c1"
    if percentage >= 75:
        return "#97CA00"
    if percentage >= 60:
        return "#dfb317"
    if percentage >= 40:
        return "#fe7d37"
    return "#e05d44"


def render_badge(label: str, value: str, color: str) -> str:
    """Return an SVG badge for ``label`` and ``value`` using ``color``."""

    label = label.strip()
    value = value.strip()
    label_width = 6 * len(label) + 20
    value_width = 6 * len(value) + 20
    total_width = label_width + value_width
    label_center = label_width / 2
    value_center = label_width + value_width / 2
    return (
        f"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{total_width}\" height=\"20\" "
        f"role=\"img\" aria-label=\"{label}: {value}\">"
        f"<title>{label}: {value}</title>"
        "<linearGradient id=\"s\" x2=\"0\" y2=\"100%\">"
        "<stop offset=\"0\" stop-color=\"#bbb\" stop-opacity=\".1\"/>"
        "<stop offset=\"1\" stop-opacity=\".1\"/>"
        "</linearGradient>"
        f"<clipPath id=\"r\"><rect width=\"{total_width}\" height=\"20\" rx=\"3\" fill=\"#fff\"/></clipPath>"
        "<g clip-path=\"url(#r)\">"
        f"<rect width=\"{label_width}\" height=\"20\" fill=\"#555\"/>"
        f"<rect x=\"{label_width}\" width=\"{value_width}\" height=\"20\" fill=\"{color}\"/>"
        f"<rect width=\"{total_width}\" height=\"20\" fill=\"url(#s)\"/>"
        "</g>"
        "<g fill=\"#fff\" text-anchor=\"middle\" font-family=\"Verdana,Geneva,DejaVu Sans,sans-serif\" font-size=\"11\">"
        f"<text x=\"{label_center:.1f}\" y=\"14\">{label}</text>"
        f"<text x=\"{value_center:.1f}\" y=\"14\">{value}</text>"
        "</g>"
        "</svg>"
    )
