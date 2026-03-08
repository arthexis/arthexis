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
    path("read/<path:doc>", views.readme, name="readme-document"),
    path("docs/", views.readme, {"prepend_docs": True}, name="docs-index"),
    path("docs/library/", views.document_library, name="docs-library"),
    path(
        "docs/<path:doc>",
        views.readme,
        {"prepend_docs": True},
        name="docs-document",
    ),
    path("apps/docs/", views.readme, name="apps-docs-index"),
    path("apps/docs/<path:doc>", views.readme, name="apps-docs-document"),
]
