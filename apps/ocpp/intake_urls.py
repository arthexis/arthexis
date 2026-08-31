from django.urls import path

from .views import intake as views

app_name = "ocpp_intake"

urlpatterns = [
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

