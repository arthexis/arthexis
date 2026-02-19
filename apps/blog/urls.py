"""Route declarations for the blog app."""

from django.urls import path

from apps.blog import views

app_name = "blog"

urlpatterns = [
    path("", views.blog_home, name="home"),
    path("post/<slug:slug>/", views.blog_post_detail, name="post-detail"),
    path("post/<slug:slug>/comment/", views.submit_comment, name="submit-comment"),
    path("category/<slug:slug>/", views.posts_by_category, name="posts-by-category"),
    path("tag/<slug:slug>/", views.posts_by_tag, name="posts-by-tag"),
]
