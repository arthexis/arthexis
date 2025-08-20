from django.contrib.contenttypes.models import ContentType
from django.utils.text import slugify

from .models import Tag, TaggedItem


def add_tag(obj, tag_name: str) -> Tag:
    """Attach a tag with the given name to ``obj``.

    The tag will be created if it does not already exist.
    """

    tag, _ = Tag.objects.get_or_create(
        name=tag_name, defaults={"slug": slugify(tag_name)}
    )
    content_type = ContentType.objects.get_for_model(obj)
    TaggedItem.objects.get_or_create(
        tag=tag, content_type=content_type, object_id=obj.pk
    )
    return tag


def get_tags(obj):
    """Return a queryset of tags attached to ``obj``."""

    content_type = ContentType.objects.get_for_model(obj)
    return Tag.objects.filter(
        tagged_items__content_type=content_type,
        tagged_items__object_id=obj.pk,
    )
