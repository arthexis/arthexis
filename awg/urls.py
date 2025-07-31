from django.urls import path
from . import views

app_name = "awg"

urlpatterns = [
    path("awg-calculator/", views.calculator, name="calculator"),
]
