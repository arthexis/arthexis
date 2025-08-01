from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="ocpp-dashboard"),
    path("simulator/", views.cp_simulator, name="cp-simulator"),
    path("chargers/", views.charger_list, name="charger-list"),
    path("chargers/<str:cid>/", views.charger_detail, name="charger-detail"),
    path("chargers/<str:cid>/action/", views.dispatch_action, name="charger-action"),
    path("c/<str:cid>/", views.charger_page, name="charger-page"),
    path("log/<str:cid>/", views.charger_log_page, name="charger-log"),
    path("c/<str:cid>/status/", views.charger_status, name="charger-status"),
]
