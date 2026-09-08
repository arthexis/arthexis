from django.urls import path

from .views import release_progress

urlpatterns = [
    path("<int:pk>/<str:action>/", release_progress, name="release-progress"),
]
