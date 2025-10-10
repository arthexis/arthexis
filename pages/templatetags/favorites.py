from django import template
from django.apps import apps
from django.contrib.contenttypes.models import ContentType

from pages.models import Favorite

register = template.Library()


@register.simple_tag
def favorite_ct_id(app_label, model_name):
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return None
    ct = ContentType.objects.get_for_model(model)
    return ct.id


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
