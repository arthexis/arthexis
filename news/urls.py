from django.urls import path

from .views import NewsArticleListView

app_name = "news"

urlpatterns = [
    path("", NewsArticleListView.as_view(), name="list"),
]
