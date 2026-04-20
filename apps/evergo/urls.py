"""URL routes for Evergo public pages."""

from django.urls import path

from . import views

app_name = "evergo"

urlpatterns = [
    path("dashboard/<uuid:token>/", views.my_evergo_dashboard, name="my-dashboard"),
    path("orders/<int:order_id>/tracking/", views.order_tracking_public, name="order-tracking-public"),
    path("customers/<uuid:public_id>/", views.customer_public_detail, name="customer-public-detail"),
    path(
        "customers/<uuid:public_id>/download.pdf",
        views.customer_pdf_download,
        name="customer-pdf-download",
    ),
]
