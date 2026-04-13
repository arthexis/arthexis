from pathlib import Path

from PIL import Image, ImageDraw

from scripts.verify_screenshot_artifacts import VerificationIssue, verify_artifacts, write_report


def _write_image(path: Path, *, size=(640, 360), color="white", text: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color=color)
    if text:
        draw = ImageDraw.Draw(image)
        draw.text((10, 10), text, fill="black")
    image.save(path)


def test_verify_artifacts_passes_for_realistic_preview(tmp_path):
    artifacts = tmp_path / "artifacts"
    _write_image(artifacts / "admin-desktop.png", text="Admin dashboard")

    (artifacts / "README.md").write_text(
        "\n".join(
            [
                "## `/admin/`",
                "- **desktop**: [admin-desktop.png](admin-desktop.png)",
            ]
        ),
        encoding="utf-8",
    )

    issues, artifact_names, expected = verify_artifacts(
        artifacts,
        min_width=320,
        min_height=240,
        min_size_bytes=500,
        stddev_floor=1.0,
    )

    assert issues == []
    assert artifact_names == ["admin-desktop.png"]
    assert expected == ["admin-desktop.png"]


def test_verify_artifacts_flags_blank_or_tiny_outputs(tmp_path):
    artifacts = tmp_path / "artifacts"
    _write_image(artifacts / "blank.png", size=(640, 360), color="white")
    _write_image(artifacts / "tiny.png", size=(80, 80), color="navy", text="x")

    issues, *_ = verify_artifacts(
        artifacts,
        min_width=320,
        min_height=240,
        min_size_bytes=1,
        stddev_floor=2.0,
    )

    reasons = "\n".join(issue.reason for issue in issues)
    assert "visually blank" in reasons
    assert "dimensions are too small" in reasons


def test_write_report_marks_readme_presence_from_artifacts_only(tmp_path):
    report_path = tmp_path / "artifacts" / "screenshot-verification.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(
        report_path=report_path,
        artifact_names=["admin-desktop.png"],
        expected=["admin-desktop.png"],
        issues=[
            VerificationIssue(
                filename="admin-desktop.png",
                reason="Image appears visually blank (near-zero color variance).",
            )
        ],
    )

    report = report_path.read_text(encoding="utf-8")
    assert "- ✅ README-listed desktop screenshots present" in report
    assert "- ❌ Non-blank image heuristic" in report
