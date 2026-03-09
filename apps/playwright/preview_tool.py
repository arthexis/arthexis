"""Utilities for deterministic development previews and screenshot diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat


class PreviewImageAnalysisError(RuntimeError):
    """Raised when an image cannot be analyzed for preview diagnostics."""


@dataclass(frozen=True)
class PreviewImageReport:
    """High-level summary that can be used to detect suspicious preview captures."""

    width: int
    height: int
    mean_brightness: float
    white_pixel_ratio: float

    def mostly_white(self, *, threshold: float = 0.9) -> bool:
        """Return whether the image has at least ``threshold`` ratio of near-white pixels."""

        return self.white_pixel_ratio >= threshold


def analyze_preview_image(path: str | Path) -> PreviewImageReport:
    """Return a coarse image report for the screenshot at ``path``.

    The report favors robust signal over pixel-perfect assertions so it can be
    used as a lightweight smoke check in development workflows.
    """

    target = Path(path)
    try:
        with Image.open(target) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            total_pixels = width * height
            white_pixels = 0
            pixels = rgb.load()
            for x in range(width):
                for y in range(height):
                    red, green, blue = pixels[x, y]
                    if red >= 245 and green >= 245 and blue >= 245:
                        white_pixels += 1
            brightness = ImageStat.Stat(rgb.convert("L")).mean[0]
    except OSError as exc:
        raise PreviewImageAnalysisError(f"Unable to analyze preview image '{target}': {exc}") from exc

    if total_pixels <= 0:
        raise PreviewImageAnalysisError(f"Preview image '{target}' has invalid dimensions {width}x{height}.")

    return PreviewImageReport(
        width=width,
        height=height,
        mean_brightness=round(float(brightness), 2),
        white_pixel_ratio=round(white_pixels / total_pixels, 4),
    )
