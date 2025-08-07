from django import template
from django.utils.safestring import mark_safe

from references.models import Reference

register = template.Library()


@register.simple_tag
def ref_img(value, size=200, alt=None):
    """Return an <img> tag with the stored reference image for the value."""
    ref, _ = Reference.objects.get_or_create(value=value)
    alt_text = alt or ref.alt_text or "reference"
    if ref.alt_text != alt_text:
        ref.alt_text = alt_text
    ref.uses += 1
    ref.save()
    return mark_safe(
        f'<img src="{ref.image.url}" width="{size}" height="{size}" alt="{ref.alt_text}" />'
    )


@register.inclusion_tag("references/footer.html")
def render_footer():
    """Render footer links for references marked to appear there."""
    return {"footer_refs": Reference.objects.filter(include_in_footer=True)}

