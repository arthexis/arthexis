"""Regression tests for consolidated release management commands."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.release import DEFAULT_PACKAGE
from apps.release.services.builder import _pep639_license_metadata, build
from apps.release.services.models import Credentials


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
