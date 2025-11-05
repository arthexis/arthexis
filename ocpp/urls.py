from django.urls import include, path

from . import views

urlpatterns = [
    path("cpms/dashboard/", views.dashboard, name="ocpp-dashboard"),
    path("evcs/simulator/", views.cp_simulator, name="cp-simulator"),
    path(
        "firmware/<int:deployment_id>/<slug:token>/",
        views.firmware_download,
        name="cp-firmware-download",
    ),
    path("chargers/", views.charger_list, name="charger-list"),
    path("chargers/<str:cid>/", views.charger_detail, name="charger-detail"),
    path(
        "chargers/<str:cid>/connector/<slug:connector>/",
        views.charger_detail,
        name="charger-detail-connector",
    ),
    path("chargers/<str:cid>/action/", views.dispatch_action, name="charger-action"),
    path(
        "chargers/<str:cid>/connector/<slug:connector>/action/",
        views.dispatch_action,
        name="charger-action-connector",
    ),
    path("c/<str:cid>/", views.charger_page, name="charger-page"),
    path(
        "c/<str:cid>/connector/<slug:connector>/",
        views.charger_page,
        name="charger-page-connector",
    ),
    path(
        "c/<str:cid>/sessions/",
        views.charger_session_search,
        name="charger-session-search",
    ),
    path(
        "c/<str:cid>/connector/<slug:connector>/sessions/",
        views.charger_session_search,
        name="charger-session-search-connector",
    ),
    path("log/<str:cid>/", views.charger_log_page, name="charger-log"),
    path(
        "log/<str:cid>/connector/<slug:connector>/",
        views.charger_log_page,
        name="charger-log-connector",
    ),
    path("c/<str:cid>/status/", views.charger_status, name="charger-status"),
    path(
        "c/<str:cid>/connector/<slug:connector>/status/",
        views.charger_status,
        name="charger-status-connector",
    ),
    path("rfid/validator/", include("ocpp.rfid.urls")),
]
