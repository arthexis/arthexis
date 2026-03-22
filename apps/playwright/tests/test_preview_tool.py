from pathlib import Path

import pytest
from PIL import Image

from apps.playwright.preview_tool import PreviewImageAnalysisError, analyze_preview_image
def test_analyze_preview_image_raises_for_missing_file(tmp_path: Path):
    with pytest.raises(PreviewImageAnalysisError):
        analyze_preview_image(tmp_path / "missing.png")
