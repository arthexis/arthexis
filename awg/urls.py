from django.urls import path

from website.views import app_index

from . import views

app_name = "awg"

urlpatterns = [
    path("", app_index, {"module": __name__}, name="index"),
    path("awg-calculator/", views.calculator, name="calculator"),
]
