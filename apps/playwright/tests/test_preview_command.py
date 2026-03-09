from pathlib import Path

import pytest
from django.core.management import call_command

from apps.playwright.management.commands.preview import Command


def test_preview_command_help_lists_expected_options(capsys):
    """The short preview command should expose the expected CLI contract."""

    with pytest.raises(SystemExit):
        call_command("preview", "--help")

    output = capsys.readouterr().out
    assert "--base-url" in output
    assert "--engine" in output
    assert "--output-dir" in output


def test_preview_command_supports_multiple_paths(settings, monkeypatch):
    """The preview command should capture each requested path into its own file."""

    settings.BASE_DIR = Path("/tmp/arthexis-test")
    captured: list[tuple[str, Path]] = []

    def fake_capture_with_fallback(**kwargs):
        output_path = kwargs["output"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xce\xb6\xd4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        captured.append((kwargs["path"], output_path))

    monkeypatch.setattr(Command, "_ensure_admin_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(Command, "_capture_with_fallback", fake_capture_with_fallback)

    call_command(
        "preview",
        "--path",
        "/admin/",
        "--path",
        "/",
        "--output-dir",
        "media/previews",
    )

    assert captured[0][0] == "/admin/"
    assert captured[0][1] == settings.BASE_DIR / "media/previews/admin.png"
    assert captured[1][0] == "/"
    assert captured[1][1] == settings.BASE_DIR / "media/previews/root.png"
