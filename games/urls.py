from django.urls import path
from . import views

app_name = "games"

urlpatterns = [
    path("", views.GameListView.as_view(), name="game-list"),
    path("<slug:slug>/", views.GameDetailView.as_view(), name="game-detail"),
]
