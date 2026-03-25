"""Tests for the rebrand management command."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from apps.core.management.commands.rebrand import LICENSE_ACKNOWLEDGEMENT


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_rebrand_rewrites_tokens_and_prunes_seed_fixtures(tmp_path):
    _write(tmp_path / "README.md", "Arthexis project https://github.com/arthexis/arthexis\n")
    _write(tmp_path / "scripts" / "service.sh", "service arthexis\n--name arthexis\n")
    _write(tmp_path / "module.py", "import arthexis\nVALUE='arthexis'\n")
    _write(tmp_path / "LICENSE", "Arthexis License text should remain\n")

    _write(tmp_path / "apps" / "core" / "fixtures" / "users__arthexis.json", "{}")
    _write(tmp_path / "apps" / "core" / "fixtures" / "releases__packagerelease_0_1_0.json", "{}")
    _write(tmp_path / "apps" / "repos" / "fixtures" / "repositories__arthexis.json", "{}")

    call_command(
        "rebrand",
        "acme",
        "--base-dir",
        str(tmp_path),
        "--service-name",
        "fleetd",
        "--repo-owner",
        "example",
        "--repo-name",
        "charge-suite",
        "--python-package",
        "acme_suite",
        "--acknowledge-license",
    )

    assert "Acme project https://github.com/example/charge-suite" in (tmp_path / "README.md").read_text(
        encoding="utf-8"
    )
    service_text = (tmp_path / "scripts" / "service.sh").read_text(encoding="utf-8")
    assert "service fleetd" in service_text
    assert "--name fleetd" in service_text

    module_text = (tmp_path / "module.py").read_text(encoding="utf-8")
    assert "import acme_suite" in module_text
    assert "'acme_suite'" in module_text

    assert (tmp_path / "LICENSE").read_text(encoding="utf-8") == "Arthexis License text should remain\n"

    assert not (tmp_path / "apps" / "core" / "fixtures" / "users__arthexis.json").exists()
    assert not (tmp_path / "apps" / "core" / "fixtures" / "releases__packagerelease_0_1_0.json").exists()
    assert not (tmp_path / "apps" / "repos" / "fixtures" / "repositories__arthexis.json").exists()


def test_rebrand_requires_license_acknowledgement_with_no_input(tmp_path):
    _write(tmp_path / "README.md", "arthexis\n")

    with pytest.raises(CommandError, match="Cannot proceed without acknowledging"):
        call_command(
            "rebrand",
            "acme",
            "--base-dir",
            str(tmp_path),
            "--no-input",
        )


def test_rebrand_interactive_license_acknowledgement(monkeypatch, tmp_path):
    _write(tmp_path / "README.md", "arthexis\n")

    monkeypatch.setattr("builtins.input", lambda _prompt: LICENSE_ACKNOWLEDGEMENT)

    call_command(
        "rebrand",
        "acme",
        "--base-dir",
        str(tmp_path),
    )

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "acme\n"
