from django.urls import path

from . import views


urlpatterns = [
    path("agent-terminals/", views.AgentTerminalListView.as_view(), name="agent-terminal-list"),
    path("agent-terminals/<int:pk>/", views.AgentTerminalDetailView.as_view(), name="agent-terminal-detail"),
]
