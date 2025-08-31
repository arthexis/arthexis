from django.urls import path
from . import views

app_name = "beta"

urlpatterns = [
    path("", views.GamePortalListView.as_view(), name="portal-list"),
    path(
        "material/<slug:slug>/",
        views.GameMaterialView.as_view(),
        name="material-detail",
    ),
    path("<slug:slug>/", views.GamePortalDetailView.as_view(), name="portal-detail"),
]
