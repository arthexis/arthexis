from django import template

register = template.Library()


@register.filter
def get_field(form, name):
    try:
        return form[name]
    except Exception:
        return None
