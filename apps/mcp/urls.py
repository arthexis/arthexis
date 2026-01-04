from django.urls import path

from . import views

app_name = "mcp"

urlpatterns = [
    path("<slug:slug>/rpc/", views.rpc_gateway, name="mcp_rpc"),
    path("<slug:slug>/events/", views.event_sink, name="mcp_events"),
    path("<slug:slug>/health/", views.health, name="mcp_health"),
]
