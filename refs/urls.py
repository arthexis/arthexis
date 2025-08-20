from django.urls import path
from . import views

app_name = "refs"

urlpatterns = [
    path("", views.recent, name="recent"),
    path("generator/", views.generator, name="generator"),
]
