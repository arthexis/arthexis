import subprocess
from pathlib import Path

import pytest

from core import views as core_views
from core.models import Package, PackageRelease


@pytest.mark.django_db
def test_promote_skips_push_when_authentication_missing(tmp_path, monkeypatch):
    package = Package.objects.create(name="pkg-auth", is_active=True)
    release = PackageRelease.objects.create(
        package=package,
        version="1.2.3",
        revision="",
    )

    log_path = tmp_path / "publish.log"
    ctx: dict[str, object] = {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_views, "_ensure_origin_main_unchanged", lambda *a, **k: None)
    monkeypatch.setattr(core_views.release_utils, "promote", lambda **kwargs: None)
    monkeypatch.setattr(core_views.PackageRelease, "dump_fixture", classmethod(lambda cls: None))
    monkeypatch.setattr(core_views, "_has_remote", lambda remote: True)
    monkeypatch.setattr(core_views, "_current_branch", lambda: "main")
    monkeypatch.setattr(core_views, "_has_upstream", lambda branch: True)

    def fake_run(cmd, *args, **kwargs):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "push"]:
            raise subprocess.CalledProcessError(
                returncode=128,
                cmd=cmd,
                stderr=(
                    "fatal: could not read Username for 'https://github.com': "
                    "No such device or address\n"
                ),
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(core_views.subprocess, "run", fake_run)

    core_views._step_promote_build(release, ctx, log_path)

    target_name = core_views._release_log_name(package.name, release.version)
    final_log = log_path.with_name(target_name)
    assert final_log.exists()
    log_text = final_log.read_text(encoding="utf-8")
    assert "Authentication is required to push release changes to origin" in log_text
    assert "could not read Username" in log_text
    assert ctx["log"] == target_name


@pytest.mark.django_db
def test_promote_raises_on_unexpected_push_failure(tmp_path, monkeypatch):
    package = Package.objects.create(name="pkg-unexpected", is_active=True)
    release = PackageRelease.objects.create(
        package=package,
        version="1.2.3",
        revision="",
    )

    log_path = tmp_path / "publish.log"
    ctx: dict[str, object] = {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core_views, "_ensure_origin_main_unchanged", lambda *a, **k: None)
    monkeypatch.setattr(core_views.release_utils, "promote", lambda **kwargs: None)
    monkeypatch.setattr(core_views.PackageRelease, "dump_fixture", classmethod(lambda cls: None))
    monkeypatch.setattr(core_views, "_has_remote", lambda remote: True)
    monkeypatch.setattr(core_views, "_current_branch", lambda: "main")
    monkeypatch.setattr(core_views, "_has_upstream", lambda branch: True)

    def fake_run(cmd, *args, **kwargs):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "push"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=cmd,
                stderr="fatal: repository not found\n",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(core_views.subprocess, "run", fake_run)
    clean_repo_called = []

    def record_clean_repo():
        clean_repo_called.append(True)

    monkeypatch.setattr(core_views, "_clean_repo", record_clean_repo)

    with pytest.raises(Exception):
        core_views._step_promote_build(release, ctx, log_path)

    assert clean_repo_called


@pytest.mark.django_db
def test_publish_commits_and_pushes_release_metadata(tmp_path, monkeypatch):
    package = Package.objects.create(name="pkg-publish", is_active=True)
    release = PackageRelease.objects.create(
        package=package,
        version="3.4.5",
        revision="",
    )

    log_name = core_views._release_log_name(package.name, release.version)
    log_path = tmp_path / log_name
    ctx: dict[str, object] = {}

    monkeypatch.chdir(tmp_path)
    fixtures_dir = tmp_path / "core" / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixtures_dir / "releases__packagerelease_3_4_5.json"
    fixture_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(core_views.release_utils, "publish", lambda **kwargs: None)
    monkeypatch.setattr(
        core_views.PackageRelease, "dump_fixture", classmethod(lambda cls: None)
    )

    relative_fixture = str(fixture_path.relative_to(tmp_path))
    commands: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        commands.append(cmd)
        if cmd[:2] == ["git", "remote"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="origin\n", stderr="")
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            target = cmd[-1]
            if "@{upstream}" in target:
                return subprocess.CompletedProcess(cmd, 0, stdout="origin/main\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f" M {relative_fixture}\n",
                stderr="",
            )
        if cmd[:2] == ["git", "add"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["git", "commit", "-m"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(core_views.subprocess, "run", fake_run)

    core_views._step_publish(release, ctx, log_path)

    log_text = log_path.read_text(encoding="utf-8")
    assert f"Committed publish metadata for v{release.version}" in log_text
    assert "Pushed release changes to origin" in log_text
    assert any(cmd[:2] == ["git", "add"] for cmd in commands)
    assert any(cmd[:2] == ["git", "push"] for cmd in commands)

