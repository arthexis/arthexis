from django.urls import path

from . import views

app_name = "links"


urlpatterns = [
    path("references/<int:reference_id>/frame/", views.reference_public_frame_view, name="reference-public-frame"),
    path("s/<slug:slug>/", views.short_url_redirect, name="short-url"),
    path("qr/<slug:slug>/", views.qr_redirect, name="qr-redirect"),
    path("qr/<slug:slug>/view/", views.qr_redirect_public_view, name="qr-redirect-public"),
]
