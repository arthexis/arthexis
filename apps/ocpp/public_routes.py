from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

PUBLIC_OCPP_ROUTES_DISABLED_MESSAGE = _(
    "The OCPP public routes are disabled for this node."
)


def reverse_public_ocpp_route(viewname, *args, **kwargs) -> str:
    try:
        return reverse(viewname, *args, **kwargs)
    except NoReverseMatch:
        return ""
