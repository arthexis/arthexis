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

