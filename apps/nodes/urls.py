from django.urls import path

from . import views

urlpatterns = [
    path("info/", views.node_info, name="node-info"),
    path("list/", views.node_list, name="node-list"),
    path("register/", views.register_node, name="register-node"),
    path(
        "register/proxy/",
        views.register_visitor_proxy,
        name="register-visitor-proxy",
    ),
    path(
        "register/telemetry/",
        views.register_visitor_telemetry,
        name="register-telemetry",
    ),
    path("screenshot/", views.capture, name="node-screenshot"),
    path("net-message/", views.net_message, name="net-message"),
    path("net-message/pull/", views.net_message_pull, name="net-message-pull"),
    path("network/chargers/", views.network_chargers, name="node-network-chargers"),
    path(
        "network/chargers/forward/",
        views.forward_chargers,
        name="node-network-forward-chargers",
    ),
    path(
        "network/chargers/action/",
        views.network_charger_action,
        name="node-network-charger-action",
    ),
]
