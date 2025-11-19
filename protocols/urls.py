from django.urls import path

from . import views

app_name = "protocols"

urlpatterns = [
    path("media/<slug:slug>/", views.media_bucket_upload, name="media-bucket-upload"),
]
