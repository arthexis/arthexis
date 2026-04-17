from django.urls import path

from .views import PublicJobsBoardView

app_name = "jobs"

urlpatterns = [
    path("", PublicJobsBoardView.as_view(), name="public-board"),
]
