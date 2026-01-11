from django.urls import path

from apps.widgets import views

app_name = "widgets"

urlpatterns = [
    path("zone/<slug:zone_slug>/", views.zone_widget_html, name="zone"),
]
