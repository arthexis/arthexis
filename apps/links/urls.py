from django.urls import path

from . import views

app_name = "links"


urlpatterns = [
    path("qr/<slug:slug>/", views.qr_redirect, name="qr-redirect"),
    path("qr/<slug:slug>/view/", views.qr_redirect_public_view, name="qr-redirect-public"),
]
