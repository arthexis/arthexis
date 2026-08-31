"""Classify release impact from deterministic repository evidence."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LEVEL_ORDER = {"patch": 1, "minor": 2, "major": 3}
PATCH_REASON = "PATCH: no major or minor policy trigger detected."


@dataclass(frozen=True)
class ImpactFinding:
    """One structured reason that contributes to release version impact."""

    level: str
    rule_id: str
    message: str
    path: str = ""
    detail: str = ""
    source: str = "auto"
    confidence: str = "high"
    downgradeable: bool = False

    @property
    def reason(self) -> str:
        return f"{self.level.upper()}: {self.message}"


@dataclass(frozen=True)
class ReleaseImpactReport:
    """Release impact evidence normalized for planner and workflow output."""

    required_bump: str
    requested_bump: str
    findings: list[ImpactFinding]

    @property
    def reasons(self) -> list[str]:
        return _dedupe(
            finding.reason
            for finding in self.findings
            if finding.level == self.required_bump
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = self.reasons
        return payload


def build_release_impact_report(
    changes: Sequence[Any],
    *,
    app_sets: tuple[set[str], set[str]] | None = None,
    requested_level: str = "auto",
) -> ReleaseImpactReport:
    """Return structured release impact evidence for changed files."""

    requested = _normalize_requested_level(requested_level)
    findings = _auto_findings(changes, app_sets=app_sets)
    auto_level = _highest_level(findings)

    if requested == "auto":
        return ReleaseImpactReport(
            required_bump=auto_level,
            requested_bump=requested,
            findings=findings,
        )

    requested_order = LEVEL_ORDER[requested]
    auto_order = LEVEL_ORDER[auto_level]
    if requested_order > auto_order:
        requested_finding = ImpactFinding(
            level=requested,
            rule_id="requested_bump_floor",
            message="requested by workflow input.",
            source="workflow-input",
        )
        return ReleaseImpactReport(
            required_bump=requested,
            requested_bump=requested,
            findings=[requested_finding, *findings],
        )
    if requested_order < auto_order:
        downgrade_finding = ImpactFinding(
            level=auto_level,
            rule_id="requested_bump_downgrade_ignored",
            message=(
                f"requested {requested} ignored because automatic impact "
                f"requires {auto_level}."
            ),
            source="workflow-input",
        )
        return ReleaseImpactReport(
            required_bump=auto_level,
            requested_bump=requested,
            findings=[*findings, downgrade_finding],
        )

    return ReleaseImpactReport(
        required_bump=auto_level,
        requested_bump=requested,
        findings=findings,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--bump-level", default="auto")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args(argv)

    from scripts.release.plan_version import collect_git_app_sets, collect_git_changes

    root = args.root.resolve()
    changes = collect_git_changes(
        root=root,
        base_ref=args.base_ref,
        head_ref=args.head_ref,
    )
    report = build_release_impact_report(
        changes,
        app_sets=collect_git_app_sets(
            root=root,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
        ),
        requested_level=args.bump_level,
    )
    payload = report.as_dict()
    payload["base_ref"] = args.base_ref
    payload["head_ref"] = args.head_ref
    payload["change_count"] = len(changes)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Required bump: {report.required_bump}")
        for reason in report.reasons:
            print(f"- {reason}")
    return 0


def _auto_findings(
    changes: Sequence[Any],
    *,
    app_sets: tuple[set[str], set[str]] | None,
) -> list[ImpactFinding]:
    findings: list[ImpactFinding] = []

    if app_sets is not None:
        before_apps, after_apps = app_sets
        for app in sorted(after_apps - before_apps):
            findings.append(
                ImpactFinding(
                    level="minor",
                    rule_id="app_added",
                    message=f"app added: apps/{app}.",
                    path=f"apps/{app}",
                )
            )
        for app in sorted(before_apps - after_apps):
            findings.append(
                ImpactFinding(
                    level="major",
                    rule_id="app_removed",
                    message=f"app removed: apps/{app}.",
                    path=f"apps/{app}",
                )
            )

    for change in changes:
        if _is_app_manifest_add_or_delete(change):
            status_name = "added" if change.status == "A" else "deleted"
            level = "minor" if change.status == "A" else "major"
            findings.append(
                ImpactFinding(
                    level=level,
                    rule_id=f"app_manifest_{status_name}",
                    message=f"app manifest {status_name}: {change.path}.",
                    path=change.path,
                )
            )
        elif _is_minor_contract_change(change):
            findings.append(
                ImpactFinding(
                    level="minor",
                    rule_id="public_contract_path",
                    message=f"public contract path: {change.path}.",
                    path=change.path,
                    confidence="medium",
                    downgradeable=True,
                )
            )
        elif _migration_creates_or_deletes_model(change):
            findings.append(
                ImpactFinding(
                    level="minor",
                    rule_id="model_lifecycle_migration",
                    message=f"model lifecycle migration: {change.path}.",
                    path=change.path,
                )
            )

    if findings:
        return findings
    return [
        ImpactFinding(
            level="patch",
            rule_id="no_major_or_minor_policy_trigger",
            message="no major or minor policy trigger detected.",
        )
    ]


def _highest_level(findings: Sequence[ImpactFinding]) -> str:
    return max((finding.level for finding in findings), key=LEVEL_ORDER.__getitem__)


def _normalize_requested_level(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"auto", *LEVEL_ORDER}:
        return normalized
    raise ValueError(f"Unsupported bump level: {value!r}")


def _is_app_manifest_add_or_delete(change: Any) -> bool:
    return (
        change.status in {"A", "D"}
        and re.fullmatch(r"apps/[^/]+/manifest\.py", change.path) is not None
    )


def _is_minor_contract_change(change: Any) -> bool:
    path = change.path
    if _is_patch_only_path(path):
        return False
    if re.fullmatch(r"apps/[^/]+/(views|forms|models|consumers|apis|api|serializers)\.py", path):
        return True
    if re.match(r"apps/[^/]+/(views|forms|models|templates|static|consumers|apis|api|serializers)/", path):
        return True
    if re.fullmatch(r"apps/[^/]+/(urls|routes|routing)\.py", path):
        return True
    if path == "config/urls.py":
        return True
    if path in {".env.example", "env.example", "sample.env"}:
        return True
    return False


def _migration_creates_or_deletes_model(change: Any) -> bool:
    if not re.fullmatch(r"apps/[^/]+/migrations/\d+_[^/]+\.py", change.path):
        return False
    return any(
        marker in change.patch
        for marker in (
            "migrations.CreateModel",
            "migrations.DeleteModel",
            "CreateModel(",
            "DeleteModel(",
        )
    )


def _is_patch_only_path(path: str) -> bool:
    if path.startswith(".github/"):
        return True
    if path.startswith("docs/") or path.startswith("tests/"):
        return True
    if re.match(r"apps/[^/]+/(tests|admin)(/|\.py)", path):
        return True
    if re.match(r"apps/[^/]+/(templates|static)(/[^/]+)*/admin/", path):
        return True
    if "/tests/" in path or path.endswith("_test.py") or path.startswith("scripts/"):
        return True
    return False


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
