import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogArticle, BlogCodeReference, BlogSigilShortcut
from apps.blog.sigils import resolve_blog_article_sigils


@pytest.mark.django_db
def test_scheduled_articles_publish_when_due(admin_user):
    article = BlogArticle.objects.create(
        title="Shipping feature flags safely",
        body="word " * 500,
        status=BlogArticle.Status.SCHEDULED,
        publish_at=timezone.now() - timezone.timedelta(minutes=2),
        author=admin_user,
    )

    result = BlogArticle.publish_ready_articles()

    article.refresh_from_db()
    assert result.published_count == 1
    assert article.status == BlogArticle.Status.PUBLISHED
    assert article.published_at is not None
    assert article.reading_time_minutes >= 2

