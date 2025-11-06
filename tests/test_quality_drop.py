from __future__ import annotations

import re
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Iterable, NamedTuple

import pytest


RE_PERCENT = re.compile(r">(\d+(?:\.\d+)?)%<")


class CoverageSnapshot(NamedTuple):
    label: str
    value: Decimal


def _parse_badge_percentage(svg_text: str, *, badge: Path) -> CoverageSnapshot:
    matches = RE_PERCENT.findall(svg_text)
    if not matches:
        pytest.fail(f"Unable to locate coverage percentage in {badge}")

    # The badges include a drop-shadow layer which duplicates the text node.
    # The final entry is the actual rendered value.
    value = Decimal(matches[-1])
    return CoverageSnapshot(label=badge.name, value=value)


def _read_badge(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_badge_from_commit(repo_root: Path, commit: str, path: Path) -> str | None:
    rel_path = path.relative_to(repo_root)
    proc = subprocess.run(
        ["git", "show", f"{commit}:{rel_path.as_posix()}"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _candidate_merge_bases(repo_root: Path) -> Iterable[str]:
    candidates = (
        ("origin/main", "HEAD"),
        ("origin/master", "HEAD"),
        ("main", "HEAD"),
        ("master", "HEAD"),
    )
    for ref, head in candidates:
        proc = subprocess.run(
            ["git", "merge-base", head, ref],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
            if commit:
                yield commit

    proc = subprocess.run(
        ["git", "rev-parse", "HEAD^"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        commit = proc.stdout.strip()
        if commit:
            yield commit


def _baseline_commit(repo_root: Path) -> str | None:
    for commit in _candidate_merge_bases(repo_root):
        return commit
    return None


def _assert_quality_drop_within_threshold(
    *, baseline: CoverageSnapshot, current: CoverageSnapshot, threshold: Decimal
) -> None:
    drop = baseline.value - current.value
    if drop <= threshold:
        return

    pytest.fail(
        "Coverage drop exceeds allowed threshold: "
        f"{baseline.label} fell by {drop:.1f}% ("
        f"{baseline.value:.1f}% -> {current.value:.1f}%) while the allowed drop is {threshold:.1f}%.",
    )


@pytest.mark.parametrize(
    "badge_name",
    [
        "coverage.svg",
        "ocpp_coverage.svg",
    ],
)
def test_quality_drop_not_exceed_three_percent(badge_name: str) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    badge_path = repo_root / badge_name

    if not badge_path.exists():
        pytest.skip(f"Badge {badge_name} is not present in the repository")

    baseline_commit = _baseline_commit(repo_root)
    if baseline_commit is None:
        pytest.skip("Unable to determine a baseline commit for coverage comparison")

    baseline_svg = _read_badge_from_commit(repo_root, baseline_commit, badge_path)
    if baseline_svg is None:
        pytest.skip(
            f"Badge {badge_name} did not exist at baseline commit {baseline_commit}"
        )

    baseline_snapshot = _parse_badge_percentage(baseline_svg, badge=badge_path)
    current_snapshot = _parse_badge_percentage(
        _read_badge(badge_path), badge=badge_path
    )

    _assert_quality_drop_within_threshold(
        baseline=baseline_snapshot,
        current=current_snapshot,
        threshold=Decimal("3.0"),
    )
