from django.urls import path

from . import views

app_name = "arts"

urlpatterns = [
    path("", views.article_detail, name="article-detail"),
    path("<slug:slug>/", views.article_detail, name="article-detail"),
]
