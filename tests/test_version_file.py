import dataclasses
from pathlib import Path
import types

import tomllib

from core import release


def test_build_writes_provided_version(monkeypatch, tmp_path):
    base = tmp_path
    (base / "requirements.txt").write_text("")
    monkeypatch.chdir(base)
    monkeypatch.setattr(release, "_git_clean", lambda: True)
    monkeypatch.setattr(release, "_write_pyproject", lambda *a, **k: None)

    release.build(version="1.2.3")

    assert (base / "VERSION").read_text().strip() == "1.2.3"


def test_pyproject_matches_version_file():
    version = Path("VERSION").read_text().strip()
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["version"] == version


def test_build_uses_package_specific_paths(monkeypatch, tmp_path):
    base = tmp_path
    custom_version = base / "pkg" / "VERSION.txt"
    custom_requirements = base / "pkg" / "deps.txt"
    custom_requirements.parent.mkdir(parents=True, exist_ok=True)
    custom_requirements.write_text("django\n# comment\nchannels\n")

    monkeypatch.chdir(base)
    monkeypatch.setattr(release, "_git_clean", lambda: True)

    captured = {}

    def fake_write_pyproject(package, version, requirements):
        captured["requirements"] = requirements

    monkeypatch.setattr(release, "_write_pyproject", fake_write_pyproject)

    package = dataclasses.replace(
        release.DEFAULT_PACKAGE,
        version_path="pkg/VERSION.txt",
        dependencies_path="pkg/deps.txt",
    )

    release.build(version="2.0.0", package=package)

    assert (base / "pkg" / "VERSION.txt").read_text().strip() == "2.0.0"
    assert not (base / "VERSION").exists()
    assert captured["requirements"] == ["django", "channels"]


def test_build_runs_custom_test_command(monkeypatch, tmp_path):
    base = tmp_path
    (base / "requirements.txt").write_text("")
    monkeypatch.chdir(base)
    monkeypatch.setattr(release, "_git_clean", lambda: True)
    monkeypatch.setattr(release, "_write_pyproject", lambda *a, **k: None)

    called = {}

    def fake_run_tests(log_path, command):
        called["command"] = command
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(release, "run_tests", fake_run_tests)

    package = dataclasses.replace(
        release.DEFAULT_PACKAGE, test_command="pytest -k unit"
    )

    release.build(version="3.1.4", tests=True, package=package)

    assert called["command"] == ["pytest", "-k", "unit"]
