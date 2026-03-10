from pathlib import Path

import pytest

pytestmark = pytest.mark.pr("PR-6152", "2026-03-10T14:30:24Z")

from PIL import Image

from apps.playwright.preview_tool import PreviewImageAnalysisError, analyze_preview_image


@pytest.mark.django_db
def test_analyze_preview_image_detects_mostly_white(tmp_path: Path):
    image_path = tmp_path / "mostly-white.png"
    Image.new("RGB", (20, 20), color=(255, 255, 255)).save(image_path)

    report = analyze_preview_image(image_path)

    assert report.width == 20
    assert report.height == 20
    assert report.mostly_white() is True


@pytest.mark.django_db
def test_analyze_preview_image_detects_non_white_image(tmp_path: Path):
    image_path = tmp_path / "dark.png"
    Image.new("RGB", (20, 20), color=(5, 5, 5)).save(image_path)

    report = analyze_preview_image(image_path)

    assert report.mostly_white() is False
    assert report.white_pixel_ratio == 0.0


def test_analyze_preview_image_raises_for_missing_file(tmp_path: Path):
    with pytest.raises(PreviewImageAnalysisError):
        analyze_preview_image(tmp_path / "missing.png")
