from django.urls import path

from . import views

app_name = "embeds"

urlpatterns = [
    path("", views.embed_card, name="embed-card"),
]
