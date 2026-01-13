from django.urls import path

from . import views

app_name = "terms"

urlpatterns = [
    path("register/", views.registration, name="registration"),
    path("<slug:slug>/", views.term_detail, name="detail"),
]
