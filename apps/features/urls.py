from __future__ import annotations

from django.urls import path

from . import views

app_name = "features"

urlpatterns = [
    path("<slug:slug>/", views.FeatureDetailView.as_view(), name="detail"),
]
