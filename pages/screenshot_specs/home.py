"""Home page screenshot specification."""

from __future__ import annotations

from .base import ScreenshotSpec, registry

registry.register(
    ScreenshotSpec(
        slug="home-readme",
        url="/",
        coverage_globs=[
            "pages/templates/pages/readme.html",
            "README*.md",
        ],
    )
)
