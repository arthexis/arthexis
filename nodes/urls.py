from django.urls import path

from . import views

urlpatterns = [
    path("info/", views.node_info, name="node-info"),
    path("list/", views.node_list, name="node-list"),
    path("register/", views.register_node, name="register-node"),
    path("screenshot/", views.capture, name="node-screenshot"),
    path("net-message/", views.net_message, name="net-message"),
    path("last-message/", views.last_net_message, name="last-net-message"),
    path("rfid/export/", views.export_rfids, name="node-rfid-export"),
    path("rfid/import/", views.import_rfids, name="node-rfid-import"),
    path("<slug:endpoint>/", views.public_node_endpoint, name="node-public-endpoint"),
]
