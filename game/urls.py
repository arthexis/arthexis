from django.urls import path
from . import views

app_name = "game"

urlpatterns = [
    path("", views.GameListView.as_view(), name="game-list"),
    path("material/<slug:slug>/", views.GameMaterialView.as_view(), name="material-detail"),
    path("region/<int:pk>/", views.follow_region, name="region-follow"),
    path("<slug:slug>/", views.GameDetailView.as_view(), name="game-detail"),
]
