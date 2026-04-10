"""Template tags for operator journey dashboard status."""

from django import template

from apps.ops.operator_journey import status_for_user

register = template.Library()


@register.simple_tag(takes_context=True)
def operator_journey_status(context):
    """Return journey status for the current request user."""

    request = context.get("request")
    if request is None:
        return {
            "has_journey": False,
            "is_complete": True,
            "message": "",
            "url": "",
        }
    return status_for_user(user=request.user)
