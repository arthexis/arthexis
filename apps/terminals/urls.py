from django.urls import path

from . import views


urlpatterns = [
    path("terminals-item/", views.TerminalsItemListView.as_view(), name="terminals-item-list"),
    path("terminals-item/<int:pk>/", views.TerminalsItemDetailView.as_view(), name="terminals-item-detail"),
]
