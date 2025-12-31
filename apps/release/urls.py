from django.urls import path

from apps.release import views

urlpatterns = [
    path("features/", views.feature_index, name="release-feature-index"),
    path(
        "admin/features/",
        views.feature_admin_index,
        name="release-feature-admin-index",
    ),
    path(
        "features/<str:package>/<slug:slug>/",
        views.feature_detail,
        name="release-feature-detail",
    ),
]
