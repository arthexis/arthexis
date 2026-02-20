from django.urls import path

from apps.meta import views

app_name = "meta"

urlpatterns = [
    path(
        "whatsapp/webhooks/<slug:route_key>/",
        views.whatsapp_webhook,
        name="whatsapp-webhook",
    ),
]
