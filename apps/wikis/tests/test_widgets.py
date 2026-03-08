"""Regression coverage for Wikipedia widget suite feature gating."""

from __future__ import annotations

from types import SimpleNamespace

from apps.wikis import widgets


def test_wikipedia_widget_returns_none_when_suite_feature_disabled(monkeypatch):
    """Regression: widget must stay hidden while Wikipedia Companion is disabled."""

    calls = []

    def _is_suite_feature_enabled(slug, *, default=True):
        calls.append((slug, default))
        return False

    monkeypatch.setattr(widgets, "is_suite_feature_enabled", _is_suite_feature_enabled)

    def _unexpected_fetch(_topic):
        raise AssertionError("fetch_wiki_summary should not be called while feature is disabled")

    monkeypatch.setattr(widgets, "fetch_wiki_summary", _unexpected_fetch)

    assert widgets.wikipedia_summary_widget(app={"name": "Open Charge Point Protocol"}) is None
    assert calls == [(widgets.WIKIPEDIA_COMPANION_FEATURE_SLUG, False)]


def test_wikipedia_widget_returns_summary_when_suite_feature_enabled(monkeypatch):
    """Widget should render summary context when the suite feature is enabled."""

    calls = []

    def _is_suite_feature_enabled(slug, *, default=True):
        calls.append((slug, default))
        return True

    monkeypatch.setattr(widgets, "is_suite_feature_enabled", _is_suite_feature_enabled)
    summary = SimpleNamespace(
        title="Open Charge Point Protocol",
        url="https://en.wikipedia.org/wiki/Open_Charge_Point_Protocol",
        first_paragraph_html="<p>Summary</p>",
    )
    monkeypatch.setattr(widgets, "fetch_wiki_summary", lambda topic: summary)

    context = widgets.wikipedia_summary_widget(app={"name": "Open Charge Point Protocol"})

    assert context == {"summary": summary, "topic": "Open Charge Point Protocol"}
    assert calls == [(widgets.WIKIPEDIA_COMPANION_FEATURE_SLUG, False)]
