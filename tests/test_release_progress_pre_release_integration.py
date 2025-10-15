import json
import subprocess
from pathlib import Path

import pytest

from core import views as core_views
from core.models import Package, PackageRelease, Todo


@pytest.mark.django_db
def test_pre_release_actions_integration(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init", "-b", "main"], check=True)
    subprocess.run(["git", "config", "user.name", "Integration Tester"], check=True)
    subprocess.run(["git", "config", "user.email", "integration@example.com"], check=True)

    script_path = Path("scripts/generate-changelog.sh")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        """#!/bin/sh
set -eu
cat <<'EOF' > CHANGELOG.rst
Changelog
=========

v1.2.0 (2024-01-01)
-------------------
- script generated entry

EOF
""",
        encoding="utf-8",
    )
    script_path.chmod(0o755)

    Path("VERSION").write_text("1.1.9\n", encoding="utf-8")
    Path("CHANGELOG.rst").write_text("Changelog\n=========\n\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit for integration test"],
        check=True,
    )

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], check=True)

    package = Package.objects.create(name="pkg", is_active=True)
    release = PackageRelease.objects.create(package=package, version="1.2.0", revision="")

    todo_fixture_dir = Path("todo_fixtures")
    monkeypatch.setattr(core_views, "TODO_FIXTURE_DIR", todo_fixture_dir)

    log_path = Path("logs") / "integration.log"

    real_run = core_views.subprocess.run
    commands: list[list[str]] = []
    fallback_mode = {"active": False}

    def recording_run(cmd, *args, **kwargs):
        commands.append(list(cmd))
        if fallback_mode["active"] and list(cmd) == ["scripts/generate-changelog.sh"]:
            backup_path = script_path.with_suffix(".bak")
            script_path.rename(backup_path)
            try:
                return real_run(cmd, *args, **kwargs)
            finally:
                if backup_path.exists():
                    backup_path.rename(script_path)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(core_views.subprocess, "run", recording_run)

    ctx: dict[str, object] = {}
    core_views._step_pre_release_actions(release, ctx, log_path)

    first_run_commands = list(commands)
    commands.clear()

    release.refresh_from_db()
    assert release.changelog == "- script generated entry"

    fixture_one = todo_fixture_dir / "todos__create_release_pkg_1_2_1.json"
    assert fixture_one.exists()
    fixture_data = json.loads(fixture_one.read_text(encoding="utf-8"))
    assert fixture_data[0]["fields"]["request"] == "Create release pkg 1.2.1"
    assert (
        fixture_data[0]["fields"]["generated_for_version"]
        == release.version
    )
    assert (
        fixture_data[0]["fields"]["generated_for_revision"]
        == release.revision
    )

    todo_one = Todo.objects.get(request="Create release pkg 1.2.1")
    assert todo_one.is_seed_data
    assert todo_one.generated_for_version == release.version
    assert todo_one.generated_for_revision == release.revision

    diff_cached = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert diff_cached.stdout.strip() == ""

    def show_commit(ref: str) -> tuple[str, list[str]]:
        result = subprocess.run(
            ["git", "show", "--pretty=%s", "--name-only", ref],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        return lines[0], lines[1:]

    head_msg, head_files = show_commit("HEAD")
    assert head_msg == "chore: add release TODO for pkg"
    assert fixture_one.as_posix() in head_files

    prev_msg, prev_files = show_commit("HEAD^")
    assert prev_msg == "pre-release commit 1.2.0"
    assert {"CHANGELOG.rst", "VERSION"}.issubset(set(prev_files))

    release_fixture_one = Path("core/fixtures/releases__packagerelease_1_2_0.json")
    assert release_fixture_one.exists()

    log_text = log_path.read_text(encoding="utf-8")
    assert "Regenerated CHANGELOG.rst using scripts/generate-changelog.sh" in log_text

    assert ["scripts/generate-changelog.sh"] in first_run_commands
    assert ["git", "commit", "-m", "pre-release commit 1.2.0"] in first_run_commands

    Path("docs").mkdir(exist_ok=True)
    Path("docs/fallback.txt").write_text("updated documentation\n", encoding="utf-8")
    subprocess.run(["git", "add", "docs/fallback.txt"], check=True)
    subprocess.run(
        ["git", "commit", "-m", "Fix fallback integration coverage (#42)"],
        check=True,
    )

    core_views._step_check_todos(
        release, {"todos_ack": True, "changelog_refreshed": True}, log_path
    )
    commands.clear()

    release.version = "1.2.1"
    release.save(update_fields=["version"])

    fallback_mode["active"] = True
    core_views._step_pre_release_actions(release, ctx, log_path)
    fallback_mode["active"] = False

    second_run_commands = list(commands)

    release.refresh_from_db()
    assert "Fix fallback integration coverage (#42)" in release.changelog

    changelog_text = Path("CHANGELOG.rst").read_text(encoding="utf-8")
    assert "Fix fallback integration coverage (#42)" in changelog_text

    fixture_two = todo_fixture_dir / "todos__create_release_pkg_1_2_2.json"
    assert fixture_two.exists()
    fixture_two_data = json.loads(fixture_two.read_text(encoding="utf-8"))
    assert fixture_two_data[0]["fields"]["request"] == "Create release pkg 1.2.2"
    assert not Todo.objects.filter(request="Create release pkg 1.2.1").exists()
    todo_two = Todo.objects.get(request="Create release pkg 1.2.2")
    assert todo_two.is_seed_data

    second_diff = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert second_diff.stdout.strip() == ""

    head_msg_two, head_files_two = show_commit("HEAD")
    assert head_msg_two == "chore: add release TODO for pkg"
    assert fixture_two.as_posix() in head_files_two

    prev_msg_two, prev_files_two = show_commit("HEAD^")
    assert prev_msg_two == "pre-release commit 1.2.1"
    assert {"CHANGELOG.rst", "VERSION"}.issubset(set(prev_files_two))

    release_fixture_two = Path("core/fixtures/releases__packagerelease_1_2_1.json")
    assert release_fixture_two.exists()
    assert not release_fixture_one.exists()

    log_after = log_path.read_text(encoding="utf-8")
    assert "Regenerated CHANGELOG.rst using Python fallback" in log_after

    assert ["scripts/generate-changelog.sh"] in second_run_commands
    assert ["git", "commit", "-m", "pre-release commit 1.2.1"] in second_run_commands

