"""Views for blog browsing and engagement."""

from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.blog.forms import BlogCommentForm
from apps.blog.models import BlogCategory, BlogPost, BlogTag


def blog_home(request: HttpRequest) -> HttpResponse:
    """Render a maximal blog landing page with featured and recent content."""
    featured_posts = BlogPost.objects.published().featured().select_related("category")[:6]
    latest_posts = BlogPost.objects.published().select_related("category").prefetch_related("tags")[:20]
    popular_tags = BlogTag.objects.annotate(post_count=Count("posts")).order_by("-post_count", "name")[:20]
    categories = BlogCategory.objects.annotate(post_count=Count("posts")).order_by("name")
    context = {
        "featured_posts": featured_posts,
        "latest_posts": latest_posts,
        "popular_tags": popular_tags,
        "categories": categories,
        "comment_form": BlogCommentForm(),
    }
    return render(request, "blog/home.html", context)


def blog_post_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Render an individual post detail page."""
    post = get_object_or_404(
        BlogPost.objects.published().select_related("category", "series").prefetch_related("tags"),
        slug=slug,
    )
    comments = post.comments.filter(is_approved=True)
    related_posts = (
        BlogPost.objects.published()
        .exclude(pk=post.pk)
        .filter(category=post.category)
        [:4]
    )
    return render(
        request,
        "blog/post_detail.html",
        {
            "post": post,
            "comments": comments,
            "comment_form": BlogCommentForm(),
            "related_posts": related_posts,
        },
    )


def posts_by_tag(request: HttpRequest, slug: str) -> HttpResponse:
    """Show all published posts under a tag."""
    tag = get_object_or_404(BlogTag, slug=slug)
    posts = tag.posts.published().select_related("category")
    return render(request, "blog/post_index.html", {"scope": f"Tag: #{tag.name}", "posts": posts})


def posts_by_category(request: HttpRequest, slug: str) -> HttpResponse:
    """Show all published posts in a category."""
    category = get_object_or_404(BlogCategory, slug=slug)
    posts = category.posts.published().select_related("category")
    return render(
        request,
        "blog/post_index.html",
        {"scope": f"Category: {category.name}", "posts": posts},
    )


@require_POST
def submit_comment(request: HttpRequest, slug: str) -> HttpResponse:
    """Receive a new comment and redirect back to the detail page."""
    post = get_object_or_404(BlogPost.objects.published(), slug=slug)
    if not post.allow_comments:
        return redirect(post.get_absolute_url())

    form = BlogCommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.post = post
        comment.save()
    return redirect(post.get_absolute_url())
