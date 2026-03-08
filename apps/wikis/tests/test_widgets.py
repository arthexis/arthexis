"""Regression coverage for Wikipedia widget suite feature gating."""

from __future__ import annotations

from types import SimpleNamespace

from apps.wikis import widgets


def test_wikipedia_widget_returns_none_when_suite_feature_disabled(monkeypatch):
    """Regression: widget must stay hidden while Wikipedia Companion is disabled."""

    monkeypatch.setattr(widgets, "is_suite_feature_enabled", lambda slug, default=False: False)

    def _unexpected_fetch(_topic):
        raise AssertionError("fetch_wiki_summary should not be called while feature is disabled")

    monkeypatch.setattr(widgets, "fetch_wiki_summary", _unexpected_fetch)

    assert widgets.wikipedia_summary_widget(app={"name": "Open Charge Point Protocol"}) is None


def test_wikipedia_widget_returns_summary_when_suite_feature_enabled(monkeypatch):
    """Widget should render summary context when the suite feature is enabled."""

    monkeypatch.setattr(widgets, "is_suite_feature_enabled", lambda slug, default=False: True)
    summary = SimpleNamespace(
        title="Open Charge Point Protocol",
        url="https://en.wikipedia.org/wiki/Open_Charge_Point_Protocol",
        first_paragraph_html="<p>Summary</p>",
    )
    monkeypatch.setattr(widgets, "fetch_wiki_summary", lambda topic: summary)

    context = widgets.wikipedia_summary_widget(app={"name": "Open Charge Point Protocol"})

    assert context == {"summary": summary, "topic": "Open Charge Point Protocol"}
