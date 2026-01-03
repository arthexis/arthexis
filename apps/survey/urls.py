from django.urls import path

from . import views

app_name = "survey"

urlpatterns = [
    path("<slug:topic_slug>/", views.survey_topic, name="topic"),
]
