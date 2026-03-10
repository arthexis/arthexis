from pathlib import Path

import pytest
from django.core.management import call_command

from apps.playwright.management.commands.preview import Command


def test_preview_command_help_lists_expected_options(capsys):
    """The preview command should expose the expected CLI contract."""

    with pytest.raises(SystemExit):
        call_command("preview", "--help")

    output = capsys.readouterr().out
    assert "--base-url" in output
    assert "--engine" in output
    assert "--output-dir" in output
    assert "--viewports" in output


def test_build_capture_plan_uses_reasonable_default_viewports() -> None:
    """The capture plan should include desktop, tablet, and mobile outputs."""

    command = Command()
    captures = command._build_capture_plan(
        paths=["/admin/environment/"],
        viewport_names=["desktop", "tablet", "mobile"],
        output=Path("/tmp/preview/admin-preview.png"),
        output_dir=Path("/tmp/preview"),
    )

    assert [capture["viewport_name"] for capture in captures] == [
        "desktop",
        "tablet",
        "mobile",
    ]
    assert captures[0]["viewport_size"] == (1440, 1800)
    assert captures[1]["viewport_size"] == (1024, 1366)
    assert captures[2]["viewport_size"] == (390, 844)
    assert captures[0]["output"] == Path("/tmp/preview/admin-preview.png")
    assert captures[1]["output"].name == "admin-environment-tablet.png"
    assert captures[2]["output"].name == "admin-environment-mobile.png"


def test_build_capture_plan_multiple_paths_includes_all_viewports() -> None:
    """Capture plan should include every requested path/viewport combination."""

    command = Command()
    captures = command._build_capture_plan(
        paths=["/admin/", "/"],
        viewport_names=["desktop", "tablet", "mobile"],
        output=Path("/tmp/preview/admin-preview.png"),
        output_dir=Path("/tmp/preview"),
    )

    assert len(captures) == 6
    outputs = {capture["output"].name for capture in captures}
    assert "admin-desktop.png" in outputs
    assert "admin-tablet.png" in outputs
    assert "admin-mobile.png" in outputs
    assert "root-desktop.png" in outputs
    assert "root-tablet.png" in outputs
    assert "root-mobile.png" in outputs
