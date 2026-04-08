from django.urls import path

from . import views

app_name = "survey"

urlpatterns = [
    path("", views.SurveyListView.as_view(), name="survey-list"),
    path("<int:pk>/", views.SurveyDetailView.as_view(), name="survey-detail"),
]
