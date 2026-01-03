from django.urls import path

from . import views

app_name = "logbook"

urlpatterns = [
    path("", views.LogbookCreateView.as_view(), name="create"),
    path("<str:secret>/", views.LogbookDetailView.as_view(), name="detail"),
    path("new/", views.LogbookCreateView.as_view(), name="create-alias"),
]
