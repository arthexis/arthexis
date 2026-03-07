"""URL configuration for hosted JavaScript extensions."""

from django.urls import path

from apps.extensions import views

app_name = "extensions"

urlpatterns = [
    path("", views.extension_catalog, name="catalog"),
    path("<slug:slug>/download.zip", views.extension_download_archive, name="download"),
    path("<slug:slug>/manifest.json", views.extension_manifest, name="manifest"),
    path("<slug:slug>/content.js", views.extension_content_script, name="content"),
    path(
        "<slug:slug>/background.js",
        views.extension_background_script,
        name="background",
    ),
    path("<slug:slug>/options.html", views.extension_options_page, name="options"),
]
