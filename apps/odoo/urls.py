from django.urls import path

from . import views

urlpatterns = [
    path(
        "query/<slug:slug>/",
        views.query_public_view,
        name="odoo-query-public-view",
    ),
]
