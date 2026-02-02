from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.http import Http404
from apps.docs import assets


def test_strip_http_subresources_removes_http_sources():
    html = (
        '<script src="http://example.com/app.js"></script>'
        '<img src="https://example.com/image.png">'
    )

    cleaned = assets.strip_http_subresources(html)

    assert "http://example.com/app.js" not in cleaned
    assert "https://example.com/image.png" in cleaned


def test_resolve_work_asset_rejects_traversal(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    user_dir = tmp_path / "work" / "bob"
    user_dir.mkdir(parents=True)
    nested_file = user_dir / "safe" / "note.txt"
    nested_file.parent.mkdir()
    nested_file.write_text("ok", encoding="utf-8")

    user = SimpleNamespace(is_authenticated=True, username="bob")

    with pytest.raises(Http404):
        assets.resolve_work_asset(user, "../secret.txt")
