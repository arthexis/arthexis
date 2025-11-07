from django.urls import path

from . import views

urlpatterns = [
    path("info/", views.node_info, name="node-info"),
    path("list/", views.node_list, name="node-list"),
    path("constellation/setup/", views.constellation_setup, name="node-constellation-setup"),
    path("register/", views.register_node, name="register-node"),
    path("screenshot/", views.capture, name="node-screenshot"),
    path("net-message/", views.net_message, name="net-message"),
    path("net-message/pull/", views.net_message_pull, name="net-message-pull"),
    path("rfid/export/", views.export_rfids, name="node-rfid-export"),
    path("rfid/import/", views.import_rfids, name="node-rfid-import"),
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
    path("proxy/session/", views.proxy_session, name="node-proxy-session"),
    path("proxy/login/<str:token>/", views.proxy_login, name="node-proxy-login"),
    path("proxy/execute/", views.proxy_execute, name="node-proxy-execute"),
    path("<slug:endpoint>/", views.public_node_endpoint, name="node-public-endpoint"),
]
