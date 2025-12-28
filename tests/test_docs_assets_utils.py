from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.http import Http404
from django.test import override_settings
from django.urls import reverse

from apps.docs import assets


@override_settings(ROOT_URLCONF="config.urls")
def test_rewrite_markdown_asset_links_rewrites_local_images():
    html = '<p><img src="static://images/logo.png"></p>'

    rewritten = assets.rewrite_markdown_asset_links(html)

    expected_url = reverse(
        "docs:readme-asset", kwargs={"source": "static", "asset": "images/logo.png"}
    )
    assert expected_url in rewritten


def test_rewrite_markdown_asset_links_ignores_non_image():
    html = '<p><img src="static://docs/readme.pdf"></p>'

    assert assets.rewrite_markdown_asset_links(html) == html


def test_strip_http_subresources_removes_http_sources():
    html = (
        '<script src="http://example.com/app.js"></script>'
        '<img src="https://example.com/image.png">'
    )

    cleaned = assets.strip_http_subresources(html)

    assert "http://example.com/app.js" not in cleaned
    assert "https://example.com/image.png" in cleaned


@override_settings(ROOT_URLCONF="config.urls")
def test_resolve_static_asset_with_custom_directory(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    asset_file = static_dir / "image.png"
    asset_file.write_bytes(b"content")

    with override_settings(STATICFILES_DIRS=[static_dir]):
        resolved = assets.resolve_static_asset("image.png")

    assert resolved == asset_file


def test_resolve_work_asset_validates_user_workspace(tmp_path, settings):
    settings.BASE_DIR = tmp_path
    user_dir = tmp_path / "work" / "alice"
    user_dir.mkdir(parents=True)
    asset_file = user_dir / "photo.png"
    asset_file.write_bytes(b"data")

    user = SimpleNamespace(is_authenticated=True, username="alice")

    resolved = assets.resolve_work_asset(user, "photo.png")

    assert resolved == asset_file


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

