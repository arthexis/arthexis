from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from django.test import override_settings

from apps.nodes.models import Node, NodeRole
from apps.sigils.models import SigilRoot
from apps.skills.models import AgentSkill, AgentSkillFile
from apps.skills.package_services import (
    PACKAGE_FORMAT,
    classify_codex_skill_file,
    export_codex_skill_package,
    import_codex_skill_package,
    materialize_codex_skill_files,
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

    sigil_scoped = classify_codex_skill_file(
        "references/glossary.md",
        "Use [CONF.BASE_DIR] and [SYS.NODE_ROLE] for local paths.",
    )
    assert sigil_scoped.portability == AgentSkillFile.Portability.PORTABLE
    assert sigil_scoped.included_by_default is True

    skill_markdown = classify_codex_skill_file(
        "SKILL.md",
        "Example path: C:\\Users\\arthexis\\.codex\\skills\\demo",
    )
    assert skill_markdown.portability == AgentSkillFile.Portability.PORTABLE
    assert skill_markdown.included_by_default is True

    unknown = classify_codex_skill_file("local-state.txt", "unknown root")
    assert unknown.portability == AgentSkillFile.Portability.DEVICE_SCOPED
    assert unknown.included_by_default is False


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


def test_scan_rejects_invalid_skill_directory_slug(tmp_path):
    skill_dir = tmp_path / "Operator Manual"
    _write(skill_dir / "SKILL.md", "---\nname: operator-manual\n---\n")

    with pytest.raises(ValueError, match="Unsafe skill slug"):
        scan_codex_skill_directory(skill_dir, dry_run=True)


@pytest.mark.django_db
def test_scan_redacts_path_blocked_files_without_reading(tmp_path, monkeypatch):
    skill_dir = tmp_path / "operator-manual"
    blocked_file = skill_dir / "logs" / "debug.log"
    _write(skill_dir / "SKILL.md", "---\nname: operator-manual\n---\n")
    _write(blocked_file, "blocked runtime log")

    original_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == blocked_file:
            raise AssertionError("path-blocked files should not be read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    summary = scan_codex_skill_directory(skill_dir, dry_run=True)

    blocked_entry = next(
        entry
        for entry in summary["files"]
        if entry["relative_path"] == "logs/debug.log"
    )
    assert blocked_entry["portability"] == AgentSkillFile.Portability.CACHE
    assert blocked_entry["included_by_default"] is False
    assert blocked_entry["size_bytes"] == 0


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
def test_materialize_writes_full_tree_resolves_sigils_and_skips_excluded(tmp_path):
    SigilRoot.objects.get_or_create(
        prefix="CONF",
        defaults={"context_type": SigilRoot.Context.CONFIG},
    )
    suite_root = tmp_path / "suite-root"
    target_root = tmp_path / "codex-skills"
    skill = AgentSkill.objects.create(
        slug="operator-manual",
        title="Operator Manual",
        markdown="Use suite root [CONF.BASE_DIR] and keep [CONF.SECRET_KEY]",
    )
    portable_files = [
        ("SKILL.md", "Stale package markdown [CONF.BASE_DIR]"),
        ("references/glossary.md", "Portable glossary for [CONF.BASE_DIR]"),
        ("scripts/setup.ps1", "Write-Output '[CONF.BASE_DIR]'"),
    ]
    for relative_path, content in portable_files:
        AgentSkillFile.objects.create(
            skill=skill,
            relative_path=relative_path,
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            portability=AgentSkillFile.Portability.PORTABLE,
            included_by_default=True,
            size_bytes=len(content.encode("utf-8")),
        )
    AgentSkillFile.objects.create(
        skill=skill,
        relative_path="credentials/token.txt",
        content="",
        content_sha256=hashlib.sha256(b"").hexdigest(),
        portability=AgentSkillFile.Portability.SECRET,
        included_by_default=False,
        exclusion_reason="secret payload",
    )
    AgentSkillFile.objects.create(
        skill=skill,
        relative_path="workgroup.md",
        content="",
        content_sha256=hashlib.sha256(b"").hexdigest(),
        portability=AgentSkillFile.Portability.STATE,
        included_by_default=False,
        exclusion_reason="runtime state is not portable",
    )

    with override_settings(BASE_DIR=suite_root):
        summary = materialize_codex_skill_files(target_root)

    skill_root = target_root / "operator-manual"
    assert summary["files_written"] == 3
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == (
        f"Use suite root {suite_root} and keep [CONF.SECRET_KEY]"
    )
    assert (skill_root / "references" / "glossary.md").read_text(
        encoding="utf-8"
    ) == f"Portable glossary for {suite_root}"
    assert (skill_root / "scripts" / "setup.ps1").read_text(encoding="utf-8") == (
        f"Write-Output '{suite_root}'"
    )
    assert not (skill_root / "credentials" / "token.txt").exists()
    assert not (skill_root / "workgroup.md").exists()

    unresolved_target_root = tmp_path / "codex-skills-unresolved"
    with override_settings(BASE_DIR=suite_root):
        unresolved_summary = materialize_codex_skill_files(
            unresolved_target_root,
            resolve_sigils_on_write=False,
        )

    unresolved_skill_root = unresolved_target_root / "operator-manual"
    assert unresolved_summary["files_written"] == 3
    assert (unresolved_skill_root / "SKILL.md").read_text(encoding="utf-8") == (
        "Use suite root [CONF.BASE_DIR] and keep [CONF.SECRET_KEY]"
    )
    assert (unresolved_skill_root / "references" / "glossary.md").read_text(
        encoding="utf-8"
    ) == "Portable glossary for [CONF.BASE_DIR]"
    assert (unresolved_skill_root / "scripts" / "setup.ps1").read_text(
        encoding="utf-8"
    ) == "Write-Output '[CONF.BASE_DIR]'"
    assert not (unresolved_skill_root / "credentials" / "token.txt").exists()
    assert not (unresolved_skill_root / "workgroup.md").exists()


@pytest.mark.django_db
def test_materialize_does_not_resolve_node_sigils_by_default(tmp_path):
    SigilRoot.objects.get_or_create(
        prefix="NODE",
        defaults={"context_type": SigilRoot.Context.ENTITY},
    )
    Node.objects.create(hostname="materialize-safe-node")
    skill = AgentSkill.objects.create(
        slug="node-sigil-safety",
        title="Node Sigil Safety",
        markdown="Node sigil [NODE.hostname] should stay literal by default.",
    )
    AgentSkillFile.objects.create(
        skill=skill,
        relative_path="SKILL.md",
        content=skill.markdown,
        content_sha256=hashlib.sha256(skill.markdown.encode("utf-8")).hexdigest(),
        portability=AgentSkillFile.Portability.PORTABLE,
        included_by_default=True,
        size_bytes=len(skill.markdown.encode("utf-8")),
    )

    target_root = tmp_path / "codex-skills"
    materialize_codex_skill_files(target_root)

    assert (target_root / "node-sigil-safety" / "SKILL.md").read_text(encoding="utf-8") == (
        "Node sigil [NODE.hostname] should stay literal by default."
    )


@pytest.mark.django_db
def test_materialize_legacy_skill_preserves_existing_portable_tree(tmp_path):
    target_root = tmp_path / "codex-skills"
    skill_root = target_root / "legacy-skill"
    _write(skill_root / "references" / "existing.md", "keep reference")
    _write(skill_root / "scripts" / "existing.ps1", "Write-Output keep")
    AgentSkill.objects.create(
        slug="legacy-skill",
        title="Legacy Skill",
        markdown="legacy markdown",
    )

    summary = materialize_codex_skill_files(target_root)

    assert summary["files_written"] == 1
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == "legacy markdown"
    assert (skill_root / "references" / "existing.md").read_text(
        encoding="utf-8"
    ) == "keep reference"
    assert (skill_root / "scripts" / "existing.ps1").read_text(
        encoding="utf-8"
    ) == "Write-Output keep"


@pytest.mark.django_db
def test_materialize_replaces_file_directory_path_collisions(tmp_path):
    target_root = tmp_path / "codex-skills"
    skill_root = target_root / "portable-skill"
    _write(skill_root / "references" / "topic" / "old.md", "old directory")
    _write(skill_root / "scripts" / "setup", "old file")
    skill = AgentSkill.objects.create(
        slug="portable-skill",
        title="Portable Skill",
        markdown="current markdown",
    )
    for relative_path, content in [
        ("references/topic", "new file"),
        ("scripts/setup/install.ps1", "Write-Output install"),
    ]:
        AgentSkillFile.objects.create(
            skill=skill,
            relative_path=relative_path,
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            portability=AgentSkillFile.Portability.PORTABLE,
            included_by_default=True,
            size_bytes=len(content.encode("utf-8")),
        )

    materialize_codex_skill_files(target_root)

    assert (skill_root / "references" / "topic").read_text(encoding="utf-8") == (
        "new file"
    )
    assert not (skill_root / "references" / "topic" / "old.md").exists()
    assert (skill_root / "scripts" / "setup" / "install.ps1").read_text(
        encoding="utf-8"
    ) == "Write-Output install"


@pytest.mark.django_db
def test_materialize_rejects_package_path_prefix_collisions(tmp_path):
    target_root = tmp_path / "codex-skills"
    skill = AgentSkill.objects.create(
        slug="portable-skill",
        title="Portable Skill",
        markdown="current markdown",
    )
    for relative_path, content in [
        ("references/topic", "parent file"),
        ("references/topic/child.md", "child file"),
    ]:
        AgentSkillFile.objects.create(
            skill=skill,
            relative_path=relative_path,
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            portability=AgentSkillFile.Portability.PORTABLE,
            included_by_default=True,
            size_bytes=len(content.encode("utf-8")),
        )

    with pytest.raises(ValueError, match="collides with nested path"):
        materialize_codex_skill_files(target_root)


@pytest.mark.django_db
def test_materialize_rejects_case_insensitive_prefix_collisions(tmp_path):
    target_root = tmp_path / "codex-skills"
    skill = AgentSkill.objects.create(
        slug="portable-skill",
        title="Portable Skill",
        markdown="current markdown",
    )
    for relative_path, content in [
        ("references/Topic", "parent file"),
        ("references/topic/child.md", "child file"),
    ]:
        AgentSkillFile.objects.create(
            skill=skill,
            relative_path=relative_path,
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            portability=AgentSkillFile.Portability.PORTABLE,
            included_by_default=True,
            size_bytes=len(content.encode("utf-8")),
        )

    with pytest.raises(ValueError, match="collides with nested path"):
        materialize_codex_skill_files(target_root)


@pytest.mark.django_db
def test_materialize_recovers_from_stale_parent_symlink(tmp_path):
    target_root = tmp_path / "codex-skills"
    skill_root = target_root / "portable-skill"
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    skill_root.mkdir(parents=True)
    try:
        (skill_root / "references").symlink_to(
            outside_root,
            target_is_directory=True,
        )
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable")
    skill = AgentSkill.objects.create(
        slug="portable-skill",
        title="Portable Skill",
        markdown="current markdown",
    )
    content = "new file"
    AgentSkillFile.objects.create(
        skill=skill,
        relative_path="references/topic.md",
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        portability=AgentSkillFile.Portability.PORTABLE,
        included_by_default=True,
        size_bytes=len(content.encode("utf-8")),
    )

    materialize_codex_skill_files(target_root)

    assert not (skill_root / "references").is_symlink()
    assert (skill_root / "references" / "topic.md").read_text(
        encoding="utf-8"
    ) == content
    assert not (outside_root / "topic.md").exists()


@pytest.mark.django_db
def test_materialize_rejects_symlinked_skill_directory(tmp_path):
    target_root = tmp_path / "codex-skills"
    target_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    try:
        (target_root / "portable-skill").symlink_to(
            outside_root,
            target_is_directory=True,
        )
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable")
    AgentSkill.objects.create(
        slug="portable-skill",
        title="Portable Skill",
        markdown="current markdown",
    )

    with pytest.raises(ValueError, match="Unsafe skill directory symlink"):
        materialize_codex_skill_files(target_root)


@pytest.mark.django_db
def test_export_synthesizes_legacy_skill_markdown_file(tmp_path):
    AgentSkill.objects.create(
        slug="legacy-skill",
        title="Legacy Skill",
        markdown="legacy markdown",
    )
    package_path = tmp_path / "legacy.zip"

    manifest = export_codex_skill_package(package_path, skill_slugs=["legacy-skill"])

    assert manifest["skills"][0]["files"] == [
        {
            "path": "SKILL.md",
            "portability": AgentSkillFile.Portability.PORTABLE,
            "included_by_default": True,
            "exclusion_reason": "",
            "content_sha256": hashlib.sha256(b"legacy markdown").hexdigest(),
        }
    ]
    with ZipFile(package_path) as package:
        assert package.read("skills/legacy-skill/SKILL.md").decode("utf-8") == (
            "legacy markdown"
        )


@pytest.mark.django_db
def test_scan_restores_soft_deleted_skill_slug(tmp_path):
    role = NodeRole.objects.create(name="Old Role", acronym="OLD")
    skill = AgentSkill.objects.create(
        slug="operator-manual",
        title="Deleted",
        markdown="old",
    )
    skill.node_roles.add(role)
    AgentSkill.all_objects.filter(pk=skill.pk).update(is_seed_data=True)
    skill.refresh_from_db()
    skill.delete()

    skill_dir = tmp_path / "operator-manual"
    _write(skill_dir / "SKILL.md", "---\nname: operator-manual\n---\n")

    scan_codex_skill_directory(skill_dir, dry_run=False)

    restored = AgentSkill.objects.get(slug="operator-manual")
    assert restored.pk == skill.pk
    assert restored.is_deleted is False
    assert restored.is_seed_data is False
    assert not restored.node_roles.exists()
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
    assert restored.is_seed_data is False


@pytest.mark.django_db
def test_import_rejects_unsafe_manifest_paths(tmp_path):
    package_path = tmp_path / "unsafe.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "unsafe",
                "title": "Unsafe",
                "files": [{"path": "../secret.txt", "included_by_default": True}],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr("skills/unsafe/../secret.txt", "secret")

    with pytest.raises(ValueError, match="Unsafe package path"):
        import_codex_skill_package(package_path, dry_run=False)


@pytest.mark.django_db
def test_import_dry_run_validates_manifest_paths(tmp_path):
    package_path = tmp_path / "unsafe-dry-run.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "unsafe",
                "title": "Unsafe",
                "files": [{"path": "../secret.txt", "included_by_default": True}],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="Unsafe package path"):
        import_codex_skill_package(package_path, dry_run=True)


@pytest.mark.django_db
def test_import_rejects_windows_absolute_manifest_paths(tmp_path):
    package_path = tmp_path / "windows-absolute.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "windows-absolute",
                "title": "Windows Absolute",
                "files": [
                    {
                        "path": "C:/Users/alice/secret.txt",
                        "included_by_default": True,
                    }
                ],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="Unsafe package path"):
        import_codex_skill_package(package_path, dry_run=True)


@pytest.mark.django_db
def test_import_dry_run_rejects_missing_manifest_files(tmp_path):
    package_path = tmp_path / "missing-file.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "missing-file",
                "title": "Missing File",
                "files": [{"path": "SKILL.md", "included_by_default": True}],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="Missing package file"):
        import_codex_skill_package(package_path, dry_run=True)


@pytest.mark.django_db
def test_import_dry_run_rejects_non_utf8_package_files(tmp_path):
    package_path = tmp_path / "invalid-encoding.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "invalid-encoding",
                "title": "Invalid Encoding",
                "files": [{"path": "SKILL.md", "included_by_default": True}],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr("skills/invalid-encoding/SKILL.md", b"\xff")

    with pytest.raises(ValueError, match="Invalid UTF-8 package file"):
        import_codex_skill_package(package_path, dry_run=True)


@pytest.mark.django_db
def test_import_rejects_duplicate_manifest_paths(tmp_path):
    package_path = tmp_path / "duplicate-path.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "duplicate-path",
                "title": "Duplicate Path",
                "files": [
                    {"path": "SKILL.md", "included_by_default": True},
                    {"path": "SKILL.md", "included_by_default": True},
                ],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr("skills/duplicate-path/SKILL.md", "demo")

    with pytest.raises(ValueError, match="Duplicate package file path"):
        import_codex_skill_package(package_path, dry_run=False)


@pytest.mark.django_db
def test_import_rejects_entries_missing_skill_markdown(tmp_path):
    package_path = tmp_path / "missing-skill-markdown.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "missing-skill-markdown",
                "title": "Missing Skill Markdown",
                "files": [
                    {"path": "references/rules.md", "included_by_default": True},
                ],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr(
            "skills/missing-skill-markdown/references/rules.md",
            "portable rules",
        )

    with pytest.raises(ValueError, match="Missing required SKILL.md"):
        import_codex_skill_package(package_path, dry_run=False)


@pytest.mark.django_db
def test_import_rejects_excluded_skill_markdown(tmp_path):
    package_path = tmp_path / "excluded-skill-markdown.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "excluded-skill-markdown",
                "title": "Excluded Skill Markdown",
                "files": [{"path": "SKILL.md", "included_by_default": False}],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr("skills/excluded-skill-markdown/SKILL.md", "demo")

    with pytest.raises(ValueError, match="SKILL.md must be included"):
        import_codex_skill_package(package_path, dry_run=False)


@pytest.mark.django_db
def test_import_rejects_duplicate_skill_slugs(tmp_path):
    package_path = tmp_path / "duplicate-slug.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "duplicate-slug",
                "title": "Duplicate Slug",
                "files": [{"path": "SKILL.md", "included_by_default": True}],
            },
            {
                "slug": "duplicate-slug",
                "title": "Duplicate Slug Again",
                "files": [{"path": "SKILL.md", "included_by_default": True}],
            },
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr("skills/duplicate-slug/SKILL.md", "demo")

    with pytest.raises(ValueError, match="Duplicate package skill slug"):
        import_codex_skill_package(package_path, dry_run=False)


@pytest.mark.django_db
def test_import_rejects_unsafe_skill_slugs(tmp_path):
    package_path = tmp_path / "unsafe-slug.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "../ops",
                "title": "Unsafe Slug",
                "files": [],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="Unsafe skill slug"):
        import_codex_skill_package(package_path, dry_run=False)


@pytest.mark.django_db
def test_import_redacts_excluded_manifest_entries(tmp_path):
    package_path = tmp_path / "excluded.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "excluded",
                "title": "Excluded",
                "files": [
                    {
                        "path": "SKILL.md",
                        "portability": AgentSkillFile.Portability.PORTABLE,
                        "included_by_default": True,
                    },
                    {
                        "path": "credentials/token.txt",
                        "portability": AgentSkillFile.Portability.SECRET,
                        "included_by_default": False,
                        "exclusion_reason": "secret payload",
                    },
                ],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr("skills/excluded/SKILL.md", "---\nname: excluded\n---\n")
        package.writestr("skills/excluded/credentials/token.txt", "secret")

    import_codex_skill_package(package_path, dry_run=False)

    file_entry = AgentSkillFile.objects.get(
        skill__slug="excluded",
        relative_path="credentials/token.txt",
    )
    assert file_entry.content == ""
    assert file_entry.size_bytes == 0
    assert file_entry.exclusion_reason == "secret payload"


@pytest.mark.django_db
def test_import_reclassifies_manifest_portability_flags(tmp_path):
    package_path = tmp_path / "misclassified.zip"
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": "misclassified",
                "title": "Misclassified",
                "files": [
                    {
                        "path": "SKILL.md",
                        "portability": AgentSkillFile.Portability.PORTABLE,
                        "included_by_default": True,
                    },
                    {
                        "path": "credentials/token.txt",
                        "portability": AgentSkillFile.Portability.PORTABLE,
                        "included_by_default": True,
                    },
                ],
            }
        ],
    }
    with ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr("skills/misclassified/SKILL.md", "---\nname: demo\n---\n")
        package.writestr("skills/misclassified/credentials/token.txt", "secret")

    import_codex_skill_package(package_path, dry_run=False)

    file_entry = AgentSkillFile.objects.get(
        skill__slug="misclassified",
        relative_path="credentials/token.txt",
    )
    assert file_entry.content == ""
    assert file_entry.portability == AgentSkillFile.Portability.SECRET
    assert file_entry.included_by_default is False


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


@pytest.mark.django_db
def test_seed_skill_sync_restores_soft_deleted_default_skill(tmp_path):
    skills_root = tmp_path / "skills"
    _write(skills_root / "cp-doctor" / "SKILL.md", "restored diagnostic skill")
    skill = AgentSkill.objects.create(
        slug="cp-doctor",
        title="Deleted",
        markdown="old",
    )
    AgentSkill.all_objects.filter(pk=skill.pk).update(is_seed_data=True)
    skill.refresh_from_db()
    skill.delete()

    with override_settings(BASE_DIR=tmp_path):
        sync_filesystem_to_db()

    restored = AgentSkill.objects.get(slug="cp-doctor")
    assert restored.pk == skill.pk
    assert restored.markdown == "restored diagnostic skill"
    assert restored.is_seed_data is True
