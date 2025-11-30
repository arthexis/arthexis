from django import template

register = template.Library()


@register.filter
def get_field(form, name):
    try:
        return form[name]
    except Exception:
        return None


@register.simple_tag
def resolve_fieldset_visibility(fieldset, controls):
    """Return the visibility configuration for the given ``fieldset``."""

    if not controls:
        return None

    name = getattr(fieldset, "name", None)
    fields = getattr(fieldset, "fields", tuple())

    for control in controls:
        if not isinstance(control, dict):
            continue

        control_name = control.get("name")
        if control_name is not None and name is not None:
            if str(control_name) == str(name):
                return control

        control_fields = control.get("fields")
        if control_fields:
            if all(field in fields for field in control_fields):
                return control

    return None
