import subprocess
from pathlib import Path

import pytest

from core import views as core_views
from core.models import Package, PackageRelease, Todo


@pytest.mark.django_db
def test_pre_release_actions_updates_version_and_fixtures(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init", "-b", "main"], check=True)
    subprocess.run(["git", "config", "user.name", "Integration Tester"], check=True)
    subprocess.run(["git", "config", "user.email", "integration@example.com"], check=True)

    version_path = Path("VERSION")
    version_path.write_text("1.1.9\n", encoding="utf-8")
    subprocess.run(["git", "add", "VERSION"], check=True)
    subprocess.run([
        "git",
        "commit",
        "-m",
        "Initial commit for integration test",
    ], check=True)

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], check=True)

    package = Package.objects.create(name="pkg", is_active=True)
    release = PackageRelease.objects.create(package=package, version="1.2.0", revision="")

    todo_fixture_dir = Path("todo_fixtures")
    monkeypatch.setattr(core_views, "TODO_FIXTURE_DIR", todo_fixture_dir)

    log_path = Path("logs") / "integration.log"
    ctx: dict[str, object] = {}

    core_views._step_pre_release_actions(release, ctx, log_path)

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
    assert head_msg == "pre-release commit 1.2.0"
    assert "VERSION" in head_files
    assert any(path.startswith("core/fixtures/") for path in head_files)

    release_fixture = Path("core/fixtures/releases__packagerelease_1_2_0.json")
    assert release_fixture.exists()
    assert not list(todo_fixture_dir.glob("todos__*.json"))
    assert not Todo.objects.filter(request__icontains="Create release pkg").exists()

    log_text = log_path.read_text(encoding="utf-8")
    assert "Committed VERSION update for 1.2.0" in log_text

    # Running the step again with the same version should not create a new commit.
    core_views._step_pre_release_actions(release, ctx, log_path)
    head_msg_after, _ = show_commit("HEAD")
    assert head_msg_after == "pre-release commit 1.2.0"
    log_text_after = log_path.read_text(encoding="utf-8")
    assert "No changes detected for VERSION; skipping commit" in log_text_after
