"""Tests for the local and CI release simulator."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.release.simulator import (
    ReleaseSimulationError,
    parse_blockers_json,
    run_release_simulation,
    write_github_output,
)


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self, *_args: Any) -> bytes:
        return self.payload


def _write_project(root: Path, *, version: str = "1.2.3", dynamic_path: str = "VERSION") -> None:
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    if dynamic_path != "VERSION":
        dynamic_file = root / dynamic_path
        dynamic_file.parent.mkdir(parents=True, exist_ok=True)
        dynamic_file.write_text(f"{version}\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "arthexis"',
                'dynamic = ["version"]',
                "",
                "[tool.setuptools.dynamic.version]",
                f'file = ["{dynamic_path}"]',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_release_simulation_can_run_without_pypi_or_build(tmp_path: Path) -> None:
    _write_project(tmp_path)

    result = run_release_simulation(
        root=tmp_path,
        skip_pypi=True,
        skip_build=True,
    )

    assert result.ok is True
    assert result.version == "1.2.3"
    assert result.failed_step == ""
    assert "authorization boundary" in result.summary_markdown
    assert [step.name for step in result.steps] == [
        "validate_version_gate",
        "preflight_pypi",
        "install_build_backend",
        "build_package",
        "validate_metadata",
        "authorization_boundary",
    ]


def test_release_simulation_reports_version_gate_mismatch(tmp_path: Path) -> None:
    _write_project(tmp_path, version="1.2.3", dynamic_path="apps/pkg/VERSION")
    (tmp_path / "apps/pkg/VERSION").write_text("1.2.4\n", encoding="utf-8")

    result = run_release_simulation(
        root=tmp_path,
        skip_pypi=True,
        skip_build=True,
    )

    assert result.ok is False
    assert result.failed_step == "validate_version_gate"
    assert "differ" in result.error
    assert "validate_version_gate" in result.summary_markdown


def test_release_simulation_reports_malformed_pyproject(tmp_path: Path) -> None:
    _write_project(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project\n", encoding="utf-8")

    result = run_release_simulation(
        root=tmp_path,
        skip_pypi=True,
        skip_build=True,
    )

    assert result.ok is False
    assert result.failed_step == "validate_version_gate"
    assert "Failed to parse pyproject file" in result.error


def test_release_simulation_quotes_package_name_for_pypi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_project(tmp_path)
    requested_urls: list[str] = []

    def fake_urlopen(url: str, *, timeout: float) -> _FakeResponse:
        requested_urls.append(url)
        assert timeout == 15.0
        return _FakeResponse(b'{"releases": {}}')

    monkeypatch.setattr("apps.release.simulator.urlopen", fake_urlopen)

    result = run_release_simulation(
        root=tmp_path,
        package_name="arthexis suite/test",
        skip_build=True,
    )

    assert result.ok is True
    assert requested_urls == ["https://pypi.org/pypi/arthexis%20suite%2Ftest/json"]


def test_release_simulation_reports_invalid_pypi_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_project(tmp_path)

    def fake_urlopen(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(b"{not json")

    monkeypatch.setattr("apps.release.simulator.urlopen", fake_urlopen)

    result = run_release_simulation(
        root=tmp_path,
        skip_build=True,
    )

    assert result.ok is False
    assert result.failed_step == "preflight_pypi"
    assert "Received invalid JSON from PyPI" in result.error


def test_release_simulation_reports_invalid_pypi_releases_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_project(tmp_path)

    def fake_urlopen(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(b'{"releases": []}')

    monkeypatch.setattr("apps.release.simulator.urlopen", fake_urlopen)

    result = run_release_simulation(
        root=tmp_path,
        skip_build=True,
    )

    assert result.ok is False
    assert result.failed_step == "preflight_pypi"
    assert "Unexpected 'releases' payload type from PyPI: list" in result.error


def test_release_simulation_does_not_read_escaped_version_file(tmp_path: Path) -> None:
    _write_project(tmp_path)
    external_version = tmp_path.parent / "external-version.txt"
    external_version.write_text("secret-version\n", encoding="utf-8")

    result = run_release_simulation(
        root=tmp_path,
        version_file=external_version,
        skip_pypi=True,
        skip_build=True,
    )

    assert result.ok is False
    assert result.failed_step == "validate_version_gate"
    assert result.version == ""
    assert "secret-version" not in result.error


def test_release_simulation_reports_escaped_dist_dir_as_build_failure(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path)

    result = run_release_simulation(
        root=tmp_path,
        dist_dir=tmp_path.parent / "dist-outside-root",
        skip_pypi=True,
    )

    assert result.ok is False
    assert result.failed_step == "build_package"
    assert "Dist directory escapes repository root" in result.error


def test_release_simulation_skips_when_blockers_are_provided(tmp_path: Path) -> None:
    result = run_release_simulation(
        root=tmp_path,
        blockers=["Open install failure issue: #1"],
    )

    assert result.ok is False
    assert result.skipped is True
    assert result.blockers == ["Open install failure issue: #1"]
    assert "SKIP" in result.summary_markdown


def test_parse_blockers_json_requires_list() -> None:
    with pytest.raises(ReleaseSimulationError, match="must be a list"):
        parse_blockers_json('{"blocker": true}')


def test_release_simulation_writes_github_outputs(tmp_path: Path) -> None:
    _write_project(tmp_path)
    output_path = tmp_path / "github-output.txt"
    result = run_release_simulation(
        root=tmp_path,
        skip_pypi=True,
        skip_build=True,
    )

    write_github_output(result, output_path)

    rendered = output_path.read_text(encoding="utf-8")
    assert "summary_markdown<<" in rendered
    assert "simulated_ok=true" in rendered
    assert "simulated_skipped=false" in rendered
    assert "failed_step=" in rendered


def test_release_simulation_avoids_github_output_delimiter_collisions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_project(tmp_path)
    output_path = tmp_path / "github-output.txt"
    result = run_release_simulation(
        root=tmp_path,
        skip_pypi=True,
        skip_build=True,
    )
    result.summary_markdown = "\n".join(
        [
            "### Simulation result",
            "ghadelim_collision",
            "- OK summary still renders.",
        ]
    )
    tokens = iter(["collision", "safe"])
    monkeypatch.setattr(
        "apps.release.simulator.secrets.token_hex",
        lambda _size: next(tokens),
    )

    write_github_output(result, output_path)

    rendered = output_path.read_text(encoding="utf-8")
    assert "summary_markdown<<ghadelim_safe" in rendered
    assert "\nghadelim_collision\n" in rendered
    assert rendered.count("\nghadelim_safe\n") == 1


def test_release_command_wraps_simulator_for_local_runs(tmp_path: Path) -> None:
    _write_project(tmp_path)
    stdout = StringIO()

    call_command(
        "release",
        "simulate",
        "--root",
        str(tmp_path),
        "--skip-pypi",
        "--skip-build",
        stdout=stdout,
    )

    assert "Release simulation reached" in stdout.getvalue()


def test_release_command_raises_on_failed_simulation(tmp_path: Path) -> None:
    _write_project(tmp_path, version="1.2.3", dynamic_path="apps/pkg/VERSION")
    (tmp_path / "apps/pkg/VERSION").write_text("1.2.4\n", encoding="utf-8")

    with pytest.raises(CommandError, match="validate_version_gate"):
        call_command(
            "release",
            "simulate",
            "--root",
            str(tmp_path),
            "--skip-pypi",
            "--skip-build",
        )
