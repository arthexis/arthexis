from django import template
from django.contrib.contenttypes.models import ContentType
from pages.models import Favorite

register = template.Library()


@register.simple_tag
def favorite_ct_id(app_label, model_name):
    try:
        ct = ContentType.objects.get_by_natural_key(app_label, model_name.lower())
        return ct.id
    except ContentType.DoesNotExist:
        return None


@register.simple_tag(takes_context=True)
def favorite_get(context, ct_id):
    user = context.get("request").user
    if not ct_id or not user.is_authenticated:
        return None
    return Favorite.objects.filter(user=user, content_type_id=ct_id).first()
