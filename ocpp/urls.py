from django.urls import path

from . import views

urlpatterns = [
    path("chargers/", views.charger_list, name="charger-list"),
    path("chargers/<str:cid>/", views.charger_detail, name="charger-detail"),
    path("chargers/<str:cid>/action/", views.dispatch_action, name="charger-action"),
]
