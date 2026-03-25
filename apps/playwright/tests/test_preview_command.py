from io import StringIO

from apps.playwright.management.commands.preview import Command


def test_print_reports_emits_manifest_after_diagnostics(monkeypatch, tmp_path) -> None:
    """Preview reports should end with a concise manifest of generated artifacts."""

    command = Command()
    command.stdout = StringIO()
    output = tmp_path / "root-desktop.png"
    output.write_bytes(b"png")

    class _Report:
        width = 1440
        height = 900
        mean_brightness = 120.5
        white_pixel_ratio = 0.25

        def mostly_white(self) -> bool:
            return False

    monkeypatch.setattr(
        "apps.playwright.management.commands.preview.analyze_preview_image",
        lambda _: _Report(),
    )
    monkeypatch.setattr(command, "_display_path", lambda path: f"shown/{path.name}")

    command._print_reports(
        [
            {
                "path": "/",
                "viewport_name": "desktop",
                "viewport_size": (1440, 1800),
                "output": output,
            }
        ]
    )

    rendered = command.stdout.getvalue()
    assert "Saved preview to:" in rendered
    assert "Preview manifest:" in rendered
    assert "- / [desktop]: shown/root-desktop.png" in rendered
