from django.urls import path
from . import views

app_name = "qrcodes"

urlpatterns = [
    path("", views.generator, name="generator"),
]
