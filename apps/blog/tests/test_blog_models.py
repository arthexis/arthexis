import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.blog.models import BlogArticle, BlogCodeReference, BlogSigilShortcut


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


@pytest.mark.django_db
def test_scheduled_article_requires_publish_date(admin_user):
    with pytest.raises(ValidationError):
        BlogArticle.objects.create(
            title="No date",
            body="hello",
            status=BlogArticle.Status.SCHEDULED,
            author=admin_user,
        )


@pytest.mark.django_db
def test_blog_code_reference_exposes_code_sigil(admin_user):
    article = BlogArticle.objects.create(title="Code citing", body="x", author=admin_user)
    ref = BlogCodeReference.objects.create(
        article=article,
        label="Feature model",
        repository_path="apps/features/models.py",
        start_line=10,
        end_line=40,
    )

    assert ref.sigil == "[CODE.apps/features/models.py:10-40]"


@pytest.mark.django_db
def test_specialized_sigil_shortcut_requires_root_and_key(admin_user):
    article = BlogArticle.objects.create(title="Sigils", body="x", author=admin_user)
    shortcut = BlogSigilShortcut(
        article=article,
        token="INVALID",
        expansion_template="Nope",
    )

    with pytest.raises(ValidationError):
        shortcut.full_clean()
