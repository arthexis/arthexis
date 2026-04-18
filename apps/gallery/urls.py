from django.urls import path

from . import views

app_name = "gallery"

urlpatterns = [
    path("", views.gallery_index, name="index"),
    path("upload/", views.gallery_upload, name="upload"),
    path("taxonomy/", views.gallery_taxonomy, name="taxonomy"),
    path("images/<uuid:slug>/", views.gallery_detail, name="detail"),
    path("images/<uuid:slug>/metadata/", views.gallery_metadata, name="metadata"),
]
