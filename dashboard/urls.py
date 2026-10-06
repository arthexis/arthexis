from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("status/", views.status_fragment, name="status-fragment"),
]
