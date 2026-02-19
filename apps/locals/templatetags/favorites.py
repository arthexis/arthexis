from django import template
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import Model
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from ..favorites_cache import get_cached_user_favorites
from ..models import Favorite

register = template.Library()


@register.simple_tag
def favorite_ct_id(app_label, model_name):
    """Return a content type id for a model.

    Kept for backwards compatibility with templates that still resolve
    content types row-by-row.
    """
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return None
    opts = getattr(model, "_meta", None)
    if opts is None:
        return None

    ct, _ = ContentType.objects.get_or_create(
        app_label=opts.app_label,
        model=opts.model_name,
    )
    return ct.id


def _collect_app_models(app_list):
    """Collect unique model classes from an app list in display order."""
    model_classes = []
    key_map = {}

    if not app_list:
        return model_classes, key_map

    for app in app_list:
        if isinstance(app, dict):
            app_label = app.get("app_label")
            models = app.get("models") or []
        else:
            app_label = getattr(app, "app_label", None)
            models = getattr(app, "models", None) or []

        if not app_label:
            continue

        for model in models:
            if isinstance(model, dict):
                object_name = model.get("object_name")
                model_app_label = model.get("app_label")
                model_class = model.get("model")
            else:
                object_name = getattr(model, "object_name", None)
                model_app_label = getattr(model, "app_label", None)
                model_class = getattr(model, "model", None)

            if not object_name:
                continue

            resolved_app_label = (
                model_app_label
                or getattr(getattr(model_class, "_meta", None), "app_label", None)
                or app_label
            )
            key = (resolved_app_label, object_name)

            if model_class is None:
                try:
                    model_class = apps.get_model(resolved_app_label, object_name)
                except LookupError:
                    model_class = None

            key_map[key] = model_class
            if model_class is not None and model_class not in model_classes:
                model_classes.append(model_class)

    return model_classes, key_map


@register.simple_tag
def favorite_ct_map(app_list):
    """Return content type ids keyed by `(app_label, object_name)` and class."""
    model_classes, key_map = _collect_app_models(app_list)
    if not model_classes:
        return {}

    ct_by_model = ContentType.objects.get_for_models(
        *model_classes,
        for_concrete_models=False,
    )
    ct_id_map = {}

    for key, model_class in key_map.items():
        if model_class is None:
            ct_id_map[key] = None
            continue
        ct = ct_by_model.get(model_class)
        ct_id = ct.id if ct else None
        ct_id_map[key] = ct_id
        ct_id_map[model_class] = ct_id

    return ct_id_map


@register.simple_tag
def favorite_ct_from_map(content_type_map, app_label, object_name, model_class=None):
    """Read a content type id from a precomputed map."""
    if not content_type_map:
        return None
    if isinstance(model_class, type) and issubclass(model_class, Model):
        return content_type_map.get(model_class)
    return content_type_map.get((app_label, object_name))


_FAVORITES_RENDER_CONTEXT_KEY = "pages:favorites_map"


def _get_favorites_map(context):
    render_context = getattr(context, "render_context", None)
    if render_context is not None and _FAVORITES_RENDER_CONTEXT_KEY in render_context:
        return render_context[_FAVORITES_RENDER_CONTEXT_KEY]

    request = context.get("request")
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        favorites = {}
    else:
        favorites = {
            favorite.content_type_id: favorite
            for favorite in Favorite.objects.filter(user=user)
        }

    if render_context is not None:
        render_context[_FAVORITES_RENDER_CONTEXT_KEY] = favorites
    return favorites


@register.simple_tag(takes_context=True)
def favorite_map(context):
    favorites = _get_favorites_map(context)
    context["favorites_map"] = favorites
    return favorites


@register.simple_tag(takes_context=True)
def favorite_get(context, ct_id):
    if not ct_id:
        return None
    favorites = _get_favorites_map(context)
    return favorites.get(ct_id)


@register.simple_tag
def favorite_from_map(favorites_map, ct_id):
    if not favorites_map or not ct_id:
        return None
    return favorites_map.get(ct_id)


@register.simple_tag
def model_app_label(model, default=None):
    if isinstance(model, dict):
        label = model.get("app_label")
        model_class = model.get("model")
    else:
        label = getattr(model, "app_label", None)
        model_class = getattr(model, "model", None)
    if not label and model_class is not None:
        meta = getattr(model_class, "_meta", None)
        label = getattr(meta, "app_label", None)
    return label or default


@register.simple_tag
def favorite_entries(app_list, favorites_map):
    if not app_list or not favorites_map:
        return []

    entries = []
    seen_ct_ids = set()
    ct_map = favorite_ct_map(app_list)

    for app in app_list:
        if isinstance(app, dict):
            app_label = app.get("app_label")
            models = app.get("models", [])
        else:
            app_label = getattr(app, "app_label", None)
            models = getattr(app, "models", None)

        if not app_label or not models:
            continue

        for model in models:
            if isinstance(model, dict):
                object_name = model.get("object_name")
                model_app_label = model.get("app_label")
                model_class = model.get("model")
            else:
                object_name = getattr(model, "object_name", None)
                model_app_label = getattr(model, "app_label", None)
                model_class = getattr(model, "model", None)
            if not object_name:
                continue

            resolved_app_label = (
                model_app_label
                or getattr(getattr(model_class, "_meta", None), "app_label", None)
                or app_label
            )
            ct_id = favorite_ct_from_map(
                ct_map,
                resolved_app_label,
                object_name,
                model_class=model_class,
            )

            if not ct_id:
                continue

            favorite = favorites_map.get(ct_id)
            if not favorite:
                continue

            if ct_id in seen_ct_ids:
                continue

            seen_ct_ids.add(ct_id)
            entries.append({
                "app": app,
                "model": model,
                "favorite": favorite,
                "ct_id": ct_id,
            })

    entries.sort(key=lambda entry: (entry["favorite"].priority, entry["favorite"].pk))

    return entries


@register.simple_tag(takes_context=True)
def cached_dashboard_favorites(context, app_list, favorites_map=None):
    request = context.get("request")
    user = getattr(request, "user", None)

    if favorites_map is None:
        favorites_map = _get_favorites_map(context)

    if not app_list or not user or not user.is_authenticated:
        return ""

    show_changelinks = bool(context.get("show_changelinks", True))
    show_model_badges = bool(context.get("show_model_badges", True))

    cached = get_cached_user_favorites(
        user.pk,
        show_changelinks=show_changelinks,
        show_model_badges=show_model_badges,
        builder=lambda: _render_favorites(
            app_list,
            favorites_map,
            show_changelinks,
            show_model_badges,
            request,
        ),
    )

    if not cached and favorites_map:
        cached = get_cached_user_favorites(
            user.pk,
            show_changelinks=show_changelinks,
            show_model_badges=show_model_badges,
            builder=lambda: _render_favorites(
                app_list,
                favorites_map,
                show_changelinks,
                show_model_badges,
                request,
            ),
            force_refresh=True,
        )

    if cached is None:
        return ""

    return mark_safe(cached)


def _render_favorites(app_list, favorites_map, show_changelinks, show_model_badges, request):
    entries = favorite_entries(app_list, favorites_map)
    if not entries:
        return ""

    return render_to_string(
        "admin/includes/dashboard_favorites_module.html",
        {
            "app_list": app_list,
            "favorite_entries": entries,
            "show_changelinks": show_changelinks,
            "show_model_badges": show_model_badges,
            "request": request,
        },
    )
