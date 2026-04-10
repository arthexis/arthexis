from django.urls import path

from . import views

app_name = "souls"

urlpatterns = [
    path("register/", views.register_landing, name="register_landing"),
    path("register/start/", views.register_start, name="register_start"),
    path("register/offering/", views.register_offering, name="register_offering"),
    path("register/survey/", views.register_survey, name="register_survey"),
    path("register/verify/<int:session_id>/<str:token>/", views.register_verify, name="register_verify"),
    path("register/complete/", views.register_complete, name="register_complete"),
    path("me/", views.soul_me, name="me"),
    path("me/download/", views.soul_download, name="download"),
    path("shop/attach/", views.attach_to_checkout, name="attach_to_checkout"),
]
