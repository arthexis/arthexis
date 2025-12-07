from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

from apps.widgets import register_widget
from apps.widgets.models import WidgetZone
from apps.wikis.services import fetch_wiki_summary


def _app_name(app: Any) -> str:
    if app is None:
        return ""
    if isinstance(app, dict):
        return str(app.get("name") or "")
    return str(getattr(app, "name", ""))


@register_widget(
    slug="wikipedia-summary",
    name=_("Wikipedia summary"),
    zone=WidgetZone.ZONE_APPLICATION,
    template_name="widgets/wiki_summary.html",
    description=_("Show a Wikipedia description for the current application."),
)
def wikipedia_summary_widget(*, app=None, **_kwargs):
    topic = (_app_name(app) or "").strip()
    if not topic:
        return None

    summary = fetch_wiki_summary(topic)
    if summary is None:
        return None

    return {"summary": summary, "topic": topic}
