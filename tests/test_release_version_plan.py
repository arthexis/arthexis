from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.release.plan_version import (
    BumpLevel,
    FileChange,
    Version,
    determine_required_bump,
    plan_release_version,
    resolve_next_version,
)
from scripts.release.release_impact import build_release_impact_report


def _write_version(root: Path, version: str) -> None:
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _init_git_repo(root: Path) -> None:
    _run_git(root, "init")
    _run_git(root, "config", "user.email", "test@example.com")
    _run_git(root, "config", "user.name", "Test User")


def test_version_bump_resets_lower_components() -> None:
    version = Version.parse("0.2.9")

    assert str(version.bump(BumpLevel.PATCH)) == "0.2.10"
    assert str(version.bump(BumpLevel.MINOR)) == "0.3.0"
    assert str(version.bump(BumpLevel.MAJOR)) == "1.0.0"


def test_policy_detects_new_app_as_minor() -> None:
    level, reasons = determine_required_bump(
        [FileChange(status="A", path="apps/kindle/manifest.py")]
    )

    assert level == BumpLevel.MINOR
    assert reasons == ["MINOR: app manifest added: apps/kindle/manifest.py."]


def test_policy_detects_removed_app_as_major() -> None:
    level, reasons = determine_required_bump(
        [FileChange(status="D", path="apps/kindle/manifest.py")]
    )

    assert level == BumpLevel.MAJOR
    assert reasons == ["MAJOR: app manifest deleted: apps/kindle/manifest.py."]


def test_policy_detects_ui_and_api_contracts_as_minor() -> None:
    level, reasons = determine_required_bump(
        [
            FileChange(status="M", path="apps/docs/views.py"),
            FileChange(status="M", path="apps/nodes/apis/status.py"),
        ]
    )

    assert level == BumpLevel.MINOR
    assert reasons == [
        "MINOR: public contract path: apps/docs/views.py.",
        "MINOR: public contract path: apps/nodes/apis/status.py.",
    ]


def test_policy_detects_project_url_contract_as_minor() -> None:
    level, reasons = determine_required_bump(
        [FileChange(status="M", path="config/urls.py")]
    )

    assert level == BumpLevel.MINOR
    assert reasons == ["MINOR: public contract path: config/urls.py."]


def test_policy_keeps_internal_settings_config_at_patch() -> None:
    level, reasons = determine_required_bump(
        [FileChange(status="M", path="config/settings/base.py")]
    )

    assert level == BumpLevel.PATCH
    assert reasons == ["PATCH: no major or minor policy trigger detected."]


def test_policy_keeps_documented_env_contracts_as_minor() -> None:
    level, reasons = determine_required_bump(
        [FileChange(status="M", path=".env.example")]
    )

    assert level == BumpLevel.MINOR
    assert reasons == ["MINOR: public contract path: .env.example."]


def test_policy_keeps_docs_and_workflows_at_patch() -> None:
    level, reasons = determine_required_bump(
        [
            FileChange(status="M", path="docs/development/package-release-process.md"),
            FileChange(status="M", path=".github/workflows/publish.yml"),
            FileChange(status="M", path="apps/docs/templates/admin/release.html"),
            FileChange(status="M", path="apps/sites/static/sites/css/admin/theme.css"),
        ]
    )

    assert level == BumpLevel.PATCH
    assert reasons == ["PATCH: no major or minor policy trigger detected."]


def test_policy_detects_model_lifecycle_migration_as_minor() -> None:
    level, reasons = determine_required_bump(
        [
            FileChange(
                status="A",
                path="apps/docs/migrations/0002_document.py",
                patch="+            migrations.CreateModel(",
            )
        ]
    )

    assert level == BumpLevel.MINOR
    assert reasons == [
        "MINOR: model lifecycle migration: apps/docs/migrations/0002_document.py."
    ]


def test_release_impact_report_exposes_structured_findings() -> None:
    report = build_release_impact_report(
        [FileChange(status="M", path="apps/docs/views.py")]
    )

    assert report.required_bump == "minor"
    assert report.reasons == ["MINOR: public contract path: apps/docs/views.py."]
    assert report.as_dict()["findings"] == [
        {
            "level": "minor",
            "rule_id": "public_contract_path",
            "message": "public contract path: apps/docs/views.py.",
            "path": "apps/docs/views.py",
            "detail": "",
            "source": "auto",
            "confidence": "medium",
            "downgradeable": True,
        }
    ]


def test_requested_bump_can_raise_automatic_policy() -> None:
    level, reasons = determine_required_bump(
        [FileChange(status="M", path="docs/development/package-release-process.md")],
        requested_level="major",
    )

    assert level == BumpLevel.MAJOR
    assert reasons == ["MAJOR: requested by workflow input."]


def test_requested_bump_cannot_downgrade_automatic_policy() -> None:
    level, reasons = determine_required_bump(
        [FileChange(status="M", path="apps/docs/views.py")],
        requested_level="patch",
    )

    assert level == BumpLevel.MINOR
    assert reasons == [
        "MINOR: public contract path: apps/docs/views.py.",
        "MINOR: requested patch ignored because automatic impact requires minor.",
    ]


def test_next_version_uses_major_policy_from_latest_release() -> None:
    next_version = resolve_next_version(
        current_version=Version.parse("0.2.9"),
        latest_release_version=Version.parse("0.2.9"),
        published_versions={Version.parse("0.2.9")},
        required_bump=BumpLevel.MAJOR,
    )

    assert str(next_version) == "1.0.0"


def test_next_version_uses_minor_policy_from_latest_release() -> None:
    next_version = resolve_next_version(
        current_version=Version.parse("0.2.9"),
        latest_release_version=Version.parse("0.2.9"),
        published_versions={Version.parse("0.2.9")},
        required_bump=BumpLevel.MINOR,
    )

    assert str(next_version) == "0.3.0"


def test_next_version_preserves_current_version_when_it_satisfies_policy() -> None:
    next_version = resolve_next_version(
        current_version=Version.parse("0.3.0"),
        latest_release_version=Version.parse("0.2.9"),
        published_versions={Version.parse("0.2.9")},
        required_bump=BumpLevel.MINOR,
    )

    assert str(next_version) == "0.3.0"


def test_planner_does_not_bump_when_no_release_changes_are_detected(
    tmp_path: Path,
) -> None:
    _write_version(tmp_path, "0.2.9")
    _init_git_repo(tmp_path)
    _run_git(tmp_path, "add", "VERSION")
    _run_git(tmp_path, "commit", "-m", "Initial version")
    _run_git(tmp_path, "tag", "v0.2.9")

    plan = plan_release_version(
        root=tmp_path,
        base_ref="v0.2.9",
        skip_pypi=True,
    )

    assert plan.release_needed is False
    assert plan.version_bumped is False
    assert plan.next_version == "0.2.9"


def test_planner_bumps_new_app_release_to_next_minor(tmp_path: Path) -> None:
    _write_version(tmp_path, "0.2.9")
    _init_git_repo(tmp_path)
    _run_git(tmp_path, "add", "VERSION")
    _run_git(tmp_path, "commit", "-m", "Initial version")
    _run_git(tmp_path, "tag", "v0.2.9")
    manifest = tmp_path / "apps" / "kindle" / "manifest.py"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("APP_NAME = 'kindle'\n", encoding="utf-8")
    _run_git(tmp_path, "add", "apps/kindle/manifest.py")
    _run_git(tmp_path, "commit", "-m", "Add Kindle app")

    plan = plan_release_version(
        root=tmp_path,
        base_ref="v0.2.9",
        published_versions=["0.2.9"],
        skip_pypi=True,
    )

    assert plan.required_bump == "minor"
    assert plan.next_version == "0.3.0"
    assert plan.impact_report["required_bump"] == "minor"
