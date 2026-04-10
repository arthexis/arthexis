"""Regression tests for consolidated release management commands."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.release import DEFAULT_PACKAGE
from apps.release.services.builder import (
    _git_modified_paths,
    _pep639_license_metadata,
    _release_metadata_paths_for_promote,
    build,
    promote,
)
from apps.release.services.models import Credentials, ReleaseError


def test_builder_release_uploads_before_pushing_git_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Regression: release uploads must complete before branch/tag pushes happen."""

    monkeypatch.chdir(tmp_path)
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("django\n", encoding="utf-8")

    events: list[tuple[str, object]] = []

    monkeypatch.setattr("apps.release.services.builder._git_clean", lambda: True)
    monkeypatch.setattr("apps.release.services.builder._git_has_staged_changes", lambda: True)
    monkeypatch.setattr(
        "apps.release.services.builder._write_pyproject",
        lambda package, version, requirements: events.append(
            ("write_pyproject", (package.name, version, tuple(requirements)))
        ),
    )
    monkeypatch.setattr(
        "apps.release.services.builder._build_in_sanitized_tree",
        lambda base_dir, *, generate_wheels: (
            (base_dir / "dist").mkdir(exist_ok=True),
            (base_dir / "dist" / "package.tar.gz").write_text("artifact", encoding="utf-8"),
            events.append(("build_dist", generate_wheels)),
        )[-1],
    )
    monkeypatch.setattr("apps.release.services.network.fetch_pypi_releases", lambda package: {})
    monkeypatch.setattr(
        "apps.release.services.uploader.upload_with_retries",
        lambda cmd, *, repository: events.append(("upload", tuple(cmd))),
    )
    monkeypatch.setattr(
        "apps.release.services.builder._run",
        lambda cmd, check=True, cwd=None: events.append(("run", tuple(cmd))) or SimpleNamespace(),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "build",
        SimpleNamespace(__file__="/tmp/site-packages/build/__init__.py"),
    )

    build(
        dist=True,
        twine=True,
        git=True,
        tag=True,
        package=DEFAULT_PACKAGE,
        creds=Credentials(token="token"),
    )

    upload_index = events.index(next(event for event in events if event[0] == "upload"))
    branch_push_index = events.index(("run", ("git", "push")))
    tag_push_index = events.index(("run", ("git", "push", "origin", "v1.2.3")))

    assert upload_index < branch_push_index
    assert upload_index < tag_push_index


@pytest.mark.parametrize(
    ("license_name", "expected_license"),
    (
        ("Arthexis Reciprocity General License 1.0", "LicenseRef-ARG-1.0"),
        ("Arthexis Reciprocity License 1.0", "LicenseRef-ARG-1.0"),
        ("MIT", "MIT"),
    ),
)
def test_pep639_license_metadata_handles_current_and_legacy_arg_names(
    license_name: str, expected_license: str
) -> None:
    """PEP 639 mapping should preserve ARG compatibility for upgraded records."""

    metadata = _pep639_license_metadata(license_name)

    assert metadata["license"] == expected_license
    if expected_license == "LicenseRef-ARG-1.0":
        assert metadata["license-files"] == ["LICENSE"]
    else:
        assert "license-files" not in metadata


def test_promote_stages_only_release_metadata_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Promotion should stage known metadata paths and avoid blanket staging."""

    commands: list[tuple[str, ...]] = []
    metadata_paths = [
        Path("VERSION"),
        Path("apps/core/fixtures/releases__packagerelease_1_2_3.json"),
        Path("pyproject.toml"),
    ]

    monkeypatch.setattr("apps.release.services.builder._git_clean", lambda: True)
    monkeypatch.setattr("apps.release.services.builder.build", lambda **kwargs: None)
    monkeypatch.setattr(
        "apps.release.services.builder._release_metadata_paths_for_promote",
        lambda package: (metadata_paths, []),
    )
    monkeypatch.setattr(
        "apps.release.services.builder._git_staged_paths",
        lambda: metadata_paths,
    )
    monkeypatch.setattr("apps.release.services.builder._git_has_staged_changes", lambda: True)
    monkeypatch.setattr(
        "apps.release.services.builder._run",
        lambda cmd, check=True, cwd=None: commands.append(tuple(cmd)) or SimpleNamespace(),
    )

    promote(version="1.2.3")

    assert ("git", "add", ".") not in commands
    assert (
        "git",
        "add",
        "VERSION",
        "apps/core/fixtures/releases__packagerelease_1_2_3.json",
        "pyproject.toml",
    ) in commands


def test_promote_fails_when_unexpected_modified_files_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Promotion should fail when unrelated working-tree edits are present."""

    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr("apps.release.services.builder._git_clean", lambda: True)
    monkeypatch.setattr("apps.release.services.builder.build", lambda **kwargs: None)
    monkeypatch.setattr(
        "apps.release.services.builder._release_metadata_paths_for_promote",
        lambda package: ([Path("VERSION")], [Path("README.tmp"), Path("apps/release/tests/test_x.py")]),
    )
    monkeypatch.setattr(
        "apps.release.services.builder._run",
        lambda cmd, check=True, cwd=None: commands.append(tuple(cmd)) or SimpleNamespace(),
    )

    with pytest.raises(ReleaseError) as exc_info:
        promote(version="1.2.3")

    message = str(exc_info.value)
    assert "Unexpected modified files detected during promotion:" in message
    assert "- README.tmp" in message
    assert "- apps/release/tests/test_x.py" in message
    assert "Clean, commit, or stash these files separately before promoting." in message
    assert ("git", "add", "VERSION") not in commands


def test_release_metadata_paths_for_promote_uses_package_version_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Promotion metadata should include package-specific version files."""

    package = SimpleNamespace(version_path="apps/pkg/VERSION")
    modified = {Path("apps/pkg/VERSION"), Path("pyproject.toml"), Path("README.md")}
    monkeypatch.setattr("apps.release.services.builder._git_modified_paths", lambda: modified)

    expected, unexpected = _release_metadata_paths_for_promote(package)

    assert expected == [Path("apps/pkg/VERSION"), Path("pyproject.toml")]
    assert unexpected == [Path("README.md")]


def test_git_modified_paths_handles_rename_and_ignored_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Modified path detection should parse NUL porcelain and ignore runtime artifacts."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARTHEXIS_LOG_DIR", str(tmp_path / "runtime-logs"))
    stdout = (
        b" M pyproject.toml\0"
        b"R  old/name.txt\0new/name -> value.txt\0"
        b"?? runtime-logs/app.log\0"
        b"?? logs/release.log\0"
    )

    def fake_run(cmd, capture_output=True, check=True):
        assert cmd == ["git", "status", "--porcelain", "-z"]
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr("apps.release.services.builder.subprocess.run", fake_run)

    assert _git_modified_paths() == {Path("pyproject.toml"), Path("new/name -> value.txt")}
