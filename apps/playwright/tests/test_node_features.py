from __future__ import annotations

from apps.playwright import node_features


def test_check_node_feature_returns_none_for_unknown_slug(tmp_path):
    """Unknown slugs should not be claimed by Playwright node feature hooks."""

    result = node_features.check_node_feature(
        "unknown",
        node=object(),
        base_dir=tmp_path,
        base_path=tmp_path,
    )

    assert result is None


def test_check_node_feature_uses_engine_availability(monkeypatch, tmp_path):
    """Playwright browser feature status should mirror engine availability checks."""

    monkeypatch.setattr(
        node_features,
        "_playwright_engine_available",
        lambda engine: engine == "chromium",
    )

    assert node_features.check_node_feature(
        "playwright-browser-chromium",
        node=object(),
        base_dir=tmp_path,
        base_path=tmp_path,
    ) is True
    assert node_features.check_node_feature(
        "playwright-browser-firefox",
        node=object(),
        base_dir=tmp_path,
        base_path=tmp_path,
    ) is False
