"""URL routes for authenticated Netmesh APIs."""

from django.urls import path

from apps.netmesh.api import views

urlpatterns = [
    path("caller/", views.caller_metadata, name="netmesh-api-caller"),
    path("peers/", views.permitted_peers, name="netmesh-api-peers"),
    path("acl/", views.acl_policy, name="netmesh-api-acl"),
    path("key-info/", views.key_info, name="netmesh-api-key-info"),
]
