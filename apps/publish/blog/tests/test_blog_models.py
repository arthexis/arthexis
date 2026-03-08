import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.publish.blog.models import BlogArticle, BlogCodeReference, BlogSigilShortcut
from apps.publish.blog.sigils import resolve_blog_article_sigils


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


@pytest.mark.django_db
def test_blog_article_absolute_url_uses_engineering_blog_path(admin_user):
    article = BlogArticle.objects.create(title="Canonical", body="x", author=admin_user)

    assert article.get_absolute_url() == f"/engineering/blog/{article.slug}/"
    assert reverse("blog-list") == "/engineering/blog/"


@pytest.mark.django_db
def test_blog_article_body_as_html_renders_markdown_and_html(admin_user):
    markdown_article = BlogArticle.objects.create(
        title="Markdown",
        body="# Heading\n\nSome **bold** text",
        body_format=BlogArticle.BodyFormat.MARKDOWN,
        author=admin_user,
    )
    html_article = BlogArticle.objects.create(
        title="HTML",
        body="<h2>Title</h2><p>Body</p>",
        body_format=BlogArticle.BodyFormat.HTML,
        author=admin_user,
    )

    assert "<h1" in markdown_article.body_as_html
    assert "<strong>bold</strong>" in markdown_article.body_as_html
    assert "<h2>Title</h2>" in html_article.body_as_html


@pytest.mark.django_db
def test_resolve_blog_article_sigils_resolves_nested_shortcuts(admin_user):
    article = BlogArticle.objects.create(title="Sigils", body="x", author=admin_user)
    BlogSigilShortcut.objects.create(article=article, token="BLOG.A", expansion_template="[BLOG.B]")
    BlogSigilShortcut.objects.create(article=article, token="BLOG.B", expansion_template="Done")

    assert resolve_blog_article_sigils("Start [BLOG.A]", article=article) == "Start Done"


@pytest.mark.django_db
def test_blog_code_reference_validates_line_range_on_save(admin_user):
    article = BlogArticle.objects.create(title="Code citing", body="x", author=admin_user)

    with pytest.raises(ValidationError):
        BlogCodeReference.objects.create(
            article=article,
            label="Invalid range",
            repository_path="apps/features/models.py",
            start_line=20,
            end_line=10,
        )


@pytest.mark.django_db
def test_resolve_blog_article_sigils_caps_growth(admin_user):
    article = BlogArticle.objects.create(title="Recursive sigil", body="x", author=admin_user)
    BlogSigilShortcut.objects.create(
        article=article,
        token="BLOG.LOOP",
        expansion_template="[BLOG.LOOP] and more",
    )

    resolved = resolve_blog_article_sigils("Start [BLOG.LOOP]", article=article)

    assert len(resolved) < 100_000
    assert resolved.count("and more") == 10
