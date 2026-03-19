"""Tests for ``scripts/helpers/pip_install.py``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture()
def pip_install_module():
    """Load the pip install helper as a module for testing.

    :return: Imported helper module.
    :raises AssertionError: If the helper module cannot be loaded.
    """
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "helpers" / "pip_install.py"
    spec = importlib.util.spec_from_file_location("pip_install_helper", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_iter_pip_output_allows_known_hardware_build_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pip_install_module,
) -> None:
    """Known Raspberry Pi hardware dependency failures should not fail installs.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param capsys: Captured stdout/stderr fixture.
    :param pip_install_module: Imported pip helper module.
    :return: ``None``.
    """

    class FakeProcess:
        """Minimal process stub for streaming pip output."""

        def __init__(self) -> None:
            """Initialize fake pip output lines."""
            self.stdout = iter(
                [
                    "Building wheel for spidev (pyproject.toml): finished with status 'error'\n",
                    "ERROR: Failed building wheel for spidev\n",
                    "Failed to build RPi.GPIO spidev\n",
                    "error: failed-wheel-build-for-install\n",
                    "╰─> RPi.GPIO, spidev\n",
                ]
            )

        def wait(self) -> int:
            """Return the simulated pip exit code."""
            return 1

    monkeypatch.setattr(pip_install_module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    assert pip_install_module._iter_pip_output(["python", "-m", "pip"]) == 0
    captured = capsys.readouterr()
    assert "Optional hardware dependencies failed to build" in captured.err
    assert "continuing install" in captured.err


def test_iter_pip_output_preserves_non_allowed_failures(
    monkeypatch: pytest.MonkeyPatch,
    pip_install_module,
) -> None:
    """Unexpected wheel build failures should keep the original failing status.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param pip_install_module: Imported pip helper module.
    :return: ``None``.
    """

    class FakeProcess:
        """Minimal process stub for a non-allowed package failure."""

        def __init__(self) -> None:
            """Initialize fake pip output lines."""
            self.stdout = iter(
                [
                    "ERROR: Failed building wheel for somepkg\n",
                    "Failed to build somepkg\n",
                ]
            )

        def wait(self) -> int:
            """Return the simulated pip exit code."""
            return 1

    monkeypatch.setattr(pip_install_module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    assert pip_install_module._iter_pip_output(["python", "-m", "pip"]) == 1


def test_extract_failed_builds_reads_pyproject_install_summary(pip_install_module) -> None:
    """The helper should parse summary lines emitted by modern pip builds.

    :param pip_install_module: Imported pip helper module.
    :return: ``None``.
    """
    assert pip_install_module._extract_failed_builds("╰─> RPi.GPIO, spidev") == {
        "RPi.GPIO",
        "spidev",
    }
