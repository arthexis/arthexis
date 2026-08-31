from django.urls import path

from . import views


app_name = "docs"


urlpatterns = [
    path(
        "read/assets/<str:source>/<path:asset>",
        views.readme_asset,
        name="readme-asset",
    ),
    path("read/", views.readme, name="readme"),
]
