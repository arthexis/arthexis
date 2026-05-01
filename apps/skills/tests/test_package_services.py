from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings

from apps.skills.models import AgentSkill, AgentSkillFile
from apps.skills.package_services import (
    classify_codex_skill_file,
    export_codex_skill_package,
    import_codex_skill_package,
    scan_codex_skill_directory,
)
from apps.skills.services import sync_filesystem_to_db


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_classifies_portable_state_secret_and_operator_scoped_files():
    assert classify_codex_skill_file(
        "SKILL.md", "---\nname: demo\n"
    ).included_by_default

    state = classify_codex_skill_file("workgroup.md", "local state")
    assert state.portability == AgentSkillFile.Portability.STATE
    assert state.included_by_default is False

    secret = classify_codex_skill_file("credentials/smtp.json", "{}")
    assert secret.portability == AgentSkillFile.Portability.SECRET
    assert secret.included_by_default is False

    operator_scoped = classify_codex_skill_file(
        "references/glossary.md",
        "Use C:\\Users\\arthexis\\.codex\\skills locally.",
    )
    assert operator_scoped.portability == AgentSkillFile.Portability.OPERATOR_SCOPED
    assert operator_scoped.included_by_default is False


@pytest.mark.django_db
def test_scan_dry_run_reports_without_writing(tmp_path):
    skill_dir = tmp_path / "operator-manual"
    _write(skill_dir / "SKILL.md", "---\nname: operator-manual\n---\n")
    _write(skill_dir / "references" / "glossary.md", "portable reference")
    _write(skill_dir / "credentials" / "smtp.json", "{}")

    summary = scan_codex_skill_directory(skill_dir, dry_run=True)

    assert summary["slug"] == "operator-manual"
    assert summary["included"] == 2
    assert summary["excluded"] == 1
    assert not AgentSkill.objects.filter(slug="operator-manual").exists()


@pytest.mark.django_db
def test_scan_preserves_custom_skills_and_redacts_excluded_content(tmp_path):
    AgentSkill.objects.create(slug="custom-existing", title="Custom", markdown="keep")
    skill_dir = tmp_path / "security-codes"
    _write(skill_dir / "SKILL.md", "---\nname: security-codes\n---\n")
    _write(skill_dir / "scripts" / "New-Code.ps1", "Write-Output ok")
    _write(skill_dir / "security-codes.json", '{"hash": "local"}')

    scan_codex_skill_directory(skill_dir, dry_run=False)

    assert AgentSkill.objects.filter(slug="custom-existing").exists()
    skill = AgentSkill.objects.get(slug="security-codes")
    assert skill.package_files.count() == 3
    state_file = skill.package_files.get(relative_path="security-codes.json")
    assert state_file.included_by_default is False
    assert state_file.content == ""


@pytest.mark.django_db
def test_export_import_round_trip_includes_only_portable_files(tmp_path):
    skill_dir = tmp_path / "quotation"
    _write(skill_dir / "SKILL.md", "---\nname: quotation\n---\n")
    _write(skill_dir / "references" / "quotation-rules.md", "portable rules")
    _write(skill_dir / "credentials" / "odoo.json", "{}")
    scan_codex_skill_directory(skill_dir, dry_run=False)

    package_path = tmp_path / "nested" / "portable-skills.zip"
    manifest = export_codex_skill_package(package_path, skill_slugs=["quotation"])
    AgentSkill.objects.filter(slug="quotation").delete()

    assert manifest["skills"][0]["slug"] == "quotation"
    assert {file["path"] for file in manifest["skills"][0]["files"]} == {
        "SKILL.md",
        "references/quotation-rules.md",
    }

    summary = import_codex_skill_package(package_path, dry_run=False)

    assert summary["skills"] == [{"slug": "quotation", "files": 2}]
    skill = AgentSkill.objects.get(slug="quotation")
    assert skill.package_files.count() == 2
    assert not skill.package_files.filter(
        relative_path="credentials/odoo.json"
    ).exists()


@pytest.mark.django_db
def test_scan_restores_soft_deleted_skill_slug(tmp_path):
    skill = AgentSkill.objects.create(
        slug="operator-manual",
        title="Deleted",
        markdown="old",
    )
    AgentSkill.all_objects.filter(pk=skill.pk).update(is_seed_data=True)
    skill.refresh_from_db()
    skill.delete()

    skill_dir = tmp_path / "operator-manual"
    _write(skill_dir / "SKILL.md", "---\nname: operator-manual\n---\n")

    scan_codex_skill_directory(skill_dir, dry_run=False)

    restored = AgentSkill.objects.get(slug="operator-manual")
    assert restored.pk == skill.pk
    assert restored.is_deleted is False
    assert (
        restored.markdown.replace("\r\n", "\n") == "---\nname: operator-manual\n---\n"
    )


@pytest.mark.django_db
def test_import_restores_soft_deleted_skill_slug(tmp_path):
    skill_dir = tmp_path / "security-codes"
    _write(skill_dir / "SKILL.md", "---\nname: security-codes\n---\n")
    scan_codex_skill_directory(skill_dir, dry_run=False)
    package_path = tmp_path / "portable-skills.zip"
    export_codex_skill_package(package_path, skill_slugs=["security-codes"])

    skill = AgentSkill.objects.get(slug="security-codes")
    AgentSkill.all_objects.filter(pk=skill.pk).update(is_seed_data=True)
    skill.refresh_from_db()
    skill.delete()

    import_codex_skill_package(package_path, dry_run=False)

    restored = AgentSkill.objects.get(slug="security-codes")
    assert restored.pk == skill.pk
    assert restored.is_deleted is False


@pytest.mark.django_db
def test_seed_skill_sync_preserves_custom_skills(tmp_path):
    skills_root = tmp_path / "skills"
    _write(skills_root / "cp-doctor" / "SKILL.md", "diagnostic skill")
    custom = AgentSkill.objects.create(
        slug="operator-manual",
        title="Operator Manual",
        markdown="custom local skill",
    )
    stale = AgentSkill.objects.create(
        slug="stale-seed",
        title="Stale Seed",
        markdown="remove",
    )
    AgentSkill.all_objects.filter(pk=stale.pk).update(is_seed_data=True)

    with override_settings(BASE_DIR=tmp_path):
        sync_filesystem_to_db()

    assert AgentSkill.objects.filter(pk=custom.pk).exists()
    assert not AgentSkill.objects.filter(pk=stale.pk).exists()
    synced = AgentSkill.objects.get(slug="cp-doctor")
    assert synced.is_seed_data is True
