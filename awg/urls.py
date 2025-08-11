from django.urls import path

from . import views

app_name = "awg"

urlpatterns = [
    path("", views.calculator, name="calculator"),
]
