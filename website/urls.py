from django.urls import path
from . import views


app_name = "website"

urlpatterns = [
    path("", views.index, name="index"),
    path("sitemap.xml", views.sitemap, name="website-sitemap"),
    path("login/", views.login_view, name="login"),
    path("rfid/", views.rfid_reader, name="rfid-reader"),
]
