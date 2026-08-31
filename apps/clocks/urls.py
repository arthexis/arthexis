from django.urls import path

from . import views

urlpatterns = [
    path(
        "clock/<slug:slug>/",
        views.public_clock_view,
        name="clockdevice-public-view",
    ),
]
