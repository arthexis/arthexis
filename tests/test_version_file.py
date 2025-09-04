from pathlib import Path

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
