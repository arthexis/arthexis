"""URL routes for Evergo public pages."""

from django.urls import path

from . import views

app_name = "evergo"

urlpatterns = [
    path("orders/<int:order_id>/tracking/", views.order_tracking_public, name="order-tracking-public"),
    path("customers/<int:pk>/", views.customer_public_detail, name="customer-public-detail"),
    path(
        "customers/<int:pk>/artifacts/<int:artifact_id>/download/",
        views.customer_artifact_download,
        name="customer-artifact-download",
    ),
]
