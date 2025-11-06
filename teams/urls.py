from django.urls import path

from .views import SlackCommandView


app_name = "teams"

urlpatterns = [
    path("slack/command/", SlackCommandView.as_view(), name="slack-command"),
]
