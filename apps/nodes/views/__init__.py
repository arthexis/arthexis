from django.apps import apps as django_apps
from django.http import JsonResponse

from .migration_status import deferred_migration_status
from .network import (
    _clean_requester_hint,
    _load_signed_node,
    import_chargers,
    net_message,
    net_message_pull,
    network_chargers,
)
from .registration import (
    _get_route_address,
    next_gway_number,
    node_info,
    node_list,
    register_node,
    register_visitor_proxy,
    register_visitor_telemetry,
    submit_enrollment_public_key,
)


def _ocpp_action_unavailable(request):
    return JsonResponse({"detail": "OCPP app is not installed."}, status=404)


if django_apps.is_installed("apps.ocpp"):
    try:
        from .ocpp import network_charger_action
    except ImportError:
        network_charger_action = _ocpp_action_unavailable
else:
    network_charger_action = _ocpp_action_unavailable


__all__ = [
    "_clean_requester_hint",
    "_get_route_address",
    "_load_signed_node",
    "deferred_migration_status",
    "import_chargers",
    "net_message",
    "net_message_pull",
    "network_charger_action",
    "network_chargers",
    "next_gway_number",
    "node_info",
    "node_list",
    "register_node",
    "register_visitor_proxy",
    "register_visitor_telemetry",
    "submit_enrollment_public_key",
]
