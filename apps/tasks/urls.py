from django.urls import path

from . import views

app_name = "tasks"

urlpatterns = [
    path("maintenance/request/", views.maintenance_request, name="maintenance-request"),
    path(
        "vendors/chargers/submit/",
        views.ChargerVendorSubmissionView.as_view(),
        name="charger-vendor-submission",
    ),
    path(
        "vendors/chargers/submit/thanks/",
        views.ChargerVendorSubmissionThanksView.as_view(),
        name="charger-vendor-submission-thanks",
    ),
]
