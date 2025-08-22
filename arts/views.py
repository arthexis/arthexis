from django.shortcuts import get_object_or_404, render

from .models import Article


def article_detail(request, slug: str | None = None):
    """Display a single article with navigation helpers."""

    if slug:
        article = get_object_or_404(Article, slug=slug)
    else:
        article = Article.objects.order_by("-created").first()
        if not article:
            return render(request, "arts/article_detail.html", {"article": None})

    calendar = Article.objects.dates("created", "month", order="DESC")
    previous_articles = (
        Article.objects.exclude(pk=article.pk).order_by("-created")[:5]
    )
    context = {
        "article": article,
        "content": article.rendered_content(),
        "calendar": calendar,
        "previous_articles": previous_articles,
    }
    return render(request, "arts/article_detail.html", context)
