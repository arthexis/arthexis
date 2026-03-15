#!/usr/bin/env python3
"""Validate preview inputs/outputs to avoid blank not-found captures."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from PIL import Image


def _iter_png_files(directory: Path) -> Iterable[Path]:
    """Yield PNG files inside one directory.

    Args:
        directory: Directory that may contain generated preview images.

    Returns:
        Iterable of PNG paths sorted by modification time.

    Raises:
        FileNotFoundError: If directory does not exist.
    """

    if not directory.exists():
        raise FileNotFoundError(f"Preview output directory does not exist: {directory}")
    files = sorted(directory.glob("*.png"), key=lambda path: path.stat().st_mtime)
    return files


def _white_pixel_ratio(image_path: Path) -> float:
    """Compute ratio of near-white pixels for one image.

    Args:
        image_path: Path to PNG screenshot.

    Returns:
        Float ratio in range [0, 1].

    Raises:
        OSError: If image cannot be opened.
    """

    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        pixels = rgb.getdata()
        total = 0
        white = 0
        for red, green, blue in pixels:
            total += 1
            if red >= 245 and green >= 245 and blue >= 245:
                white += 1
    return float(white) / float(total or 1)


def _contains_not_found_markers(html_path: Path) -> bool:
    """Return whether one HTML file appears to be a not-found page.

    Args:
        html_path: Temporary HTML file captured from preview URL.

    Returns:
        ``True`` when not-found markers are detected.

    Raises:
        FileNotFoundError: If HTML file does not exist.
    """

    html_text = html_path.read_text(encoding="utf-8", errors="ignore").lower()
    markers = ["not found", "404", "page not found"]
    return any(marker in html_text for marker in markers)


def main() -> None:
    """Parse command-line arguments and enforce preview quality checks."""

    parser = argparse.ArgumentParser(
        description="Validate preview URL/body and screenshot output.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Preview output directory containing PNG artifacts.",
    )
    parser.add_argument(
        "--max-white-ratio",
        type=float,
        default=0.97,
        help="Maximum allowed near-white pixel ratio before failing.",
    )
    parser.add_argument(
        "--html-file",
        default="",
        help="Optional HTML file to scan for not-found markers.",
    )
    args = parser.parse_args()

    if args.html_file:
        html_path = Path(args.html_file)
        if _contains_not_found_markers(html_path):
            raise SystemExit(
                f"Preview guard failed: detected not-found markers in {html_path}."
            )

    output_dir = Path(args.output_dir)
    png_files = list(_iter_png_files(output_dir))
    if not png_files:
        raise SystemExit(f"Preview guard failed: no PNG screenshots in {output_dir}.")

    target_image = png_files[-1]
    white_ratio = _white_pixel_ratio(target_image)
    if white_ratio > args.max_white_ratio:
        raise SystemExit(
            "Preview guard failed: screenshot looks blank/white "
            f"({white_ratio:.3f} > {args.max_white_ratio:.3f}) at {target_image}."
        )

    print(
        f"Preview guard passed for {target_image} with white ratio {white_ratio:.3f}."
    )


if __name__ == "__main__":
    main()
