from pathlib import Path

import tomllib

from core import release


def test_build_respects_custom_package_list(monkeypatch, tmp_path):
    base = tmp_path
    (base / "requirements.txt").write_text("")
    monkeypatch.chdir(base)
    monkeypatch.setattr(release, "_git_clean", lambda: True)

    package = release.Package(
        name="custom-arthexis",
        description="Custom build",
        author="Art Hex",
        email="release@example.com",
        python_requires=">=3.11",
        license="MIT",
        packages=["pkg_alpha", "pkg_beta.sub"],
    )

    release.build(package=package, version="1.2.3")

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["tool"]["setuptools"]["packages"] == [
        "pkg_alpha",
        "pkg_beta.sub",
    ]
