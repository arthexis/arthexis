"""Tests for the rebrand management command."""

from __future__ import annotations

from pathlib import Path

from django.core.management import call_command


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_rebrand_updates_single_quoted_project_version(tmp_path):
    _write(
        tmp_path / "pyproject.toml",
        (
            "[project]\n"
            "name = 'arthexis'\n"
            "version = '0.5.0'\n"
        ),
    )

    call_command(
        "rebrand",
        "acme",
        "--base-dir",
        str(tmp_path),
        "--project-version",
        "1.2.3",
        "--acknowledge-license",
    )

    pyproject_lines = (tmp_path / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    assert "name = 'acme'" in pyproject_lines
    assert "version = '1.2.3'" in pyproject_lines
