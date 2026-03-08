from django.urls import path

from .views import SlackBotOAuthCallbackView, SlackCommandView


app_name = "teams"

urlpatterns = [
    path(
        "slack/oauth/callback/",
        SlackBotOAuthCallbackView.as_view(),
        name="slack-bot-callback",
    ),
    path("slack/command/", SlackCommandView.as_view(), name="slack-command"),
]
