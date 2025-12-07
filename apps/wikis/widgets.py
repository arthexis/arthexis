from __future__ import annotations

from typing import Any

from urllib.parse import unquote, urlparse

from django.apps import apps as django_apps
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


def _app_label(app: Any) -> str:
    if app is None:
        return ""
    if isinstance(app, dict):
        return str(app.get("app_label") or "")
    return str(getattr(app, "app_label", ""))


def _wiki_title_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    path = (parsed.path or "").strip("/")
    if not path:
        return ""
    if path.lower().startswith("wiki/"):
        path = path[5:]
    path = path.strip()
    if not path:
        return ""
    return unquote(path.replace("_", " ")).strip()


@register_widget(
    slug="wikipedia-summary",
    name=_("Wikipedia summary"),
    zone=WidgetZone.ZONE_APPLICATION,
    template_name="widgets/wiki_summary.html",
    description=_("Show a Wikipedia description for the current application."),
)
def wikipedia_summary_widget(*, app=None, **_kwargs):
    topic = (_app_name(app) or "").strip()
    wiki_url = ""
    app_label = (_app_label(app) or "").strip()
    if app_label:
        try:
            ApplicationModel = django_apps.get_model("app", "ApplicationModel")
        except LookupError:
            ApplicationModel = None
        if ApplicationModel is not None:
            model_with_wiki = (
                ApplicationModel.objects.filter(
                    application__name=app_label, wiki_url__isnull=False
                )
                .exclude(wiki_url="")
                .order_by("label")
                .first()
            )
            if model_with_wiki:
                wiki_url = model_with_wiki.wiki_url

    topic_override = _wiki_title_from_url(wiki_url)
    if topic_override:
        topic = topic_override
    if not topic:
        return None

    summary = fetch_wiki_summary(topic)
    if summary is None:
        return None

    return {"summary": summary, "topic": topic}
