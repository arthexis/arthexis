from pathlib import Path

from apps.content import utils


def test_get_content_drop_dir_defaults_to_media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    if hasattr(settings, "CONTENT_DROP_DIR"):
        delattr(settings, "CONTENT_DROP_DIR")

    content_drop_dir = utils._get_content_drop_dir()

    assert content_drop_dir == Path(settings.MEDIA_ROOT) / "content-drops"


def test_get_content_drop_dir_honors_content_drop_dir_setting(settings, tmp_path):
    settings.CONTENT_DROP_DIR = tmp_path / "custom-content-drops"

    content_drop_dir = utils._get_content_drop_dir()

    assert content_drop_dir == Path(settings.CONTENT_DROP_DIR)
