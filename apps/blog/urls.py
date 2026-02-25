from django.urls import path

from apps.blog.views import BlogArticleDetailView, BlogArticleListView

urlpatterns = [
    path("engineering/blog/", BlogArticleListView.as_view(), name="blog-list"),
    path("engineering/blog/<slug:slug>/", BlogArticleDetailView.as_view(), name="blog-detail"),
]
