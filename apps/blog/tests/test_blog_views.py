"""Regression tests for the blog app's main experience."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogCategory, BlogPost, BlogTag


class BlogViewsTests(TestCase):
    """Validate primary blog pages and interactions."""

    def setUp(self) -> None:
        """Create shared post fixtures for each test."""
        self.user = get_user_model().objects.create_user(
            username="writer",
            email="writer@example.com",
            password="pass12345",
        )
        self.category = BlogCategory.objects.create(name="Engineering")
        self.tag = BlogTag.objects.create(name="django")
        self.post = BlogPost.objects.create(
            title="Maximal blogging with Arthexis",
            subtitle="Everything and the kitchen sink",
            summary="A complete guide to building a maximal blog.",
            body="Massive body content",
            status=BlogPost.Status.PUBLISHED,
            published_at=timezone.now(),
            author=self.user,
            category=self.category,
            is_featured=True,
        )
        self.post.tags.add(self.tag)

    def test_blog_home_lists_published_posts(self) -> None:
        """Home page should surface a published post."""
        response = self.client.get(reverse("blog:home"))
        self.assertContains(response, self.post.title)

    def test_post_detail_renders_content(self) -> None:
        """Detail page should display the selected post body."""
        response = self.client.get(reverse("blog:post-detail", kwargs={"slug": self.post.slug}))
        self.assertContains(response, "Massive body content")

    def test_comment_submission_creates_unapproved_comment(self) -> None:
        """Submitting a comment stores it for later moderation."""
        response = self.client.post(
            reverse("blog:submit-comment", kwargs={"slug": self.post.slug}),
            data={
                "author_name": "Reader",
                "author_email": "reader@example.com",
                "body": "Incredible post!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.post.comments.count(), 1)
        self.assertFalse(self.post.comments.first().is_approved)

    def test_tag_and_category_filters(self) -> None:
        """Filter pages should include the published post."""
        tag_response = self.client.get(reverse("blog:posts-by-tag", kwargs={"slug": self.tag.slug}))
        category_response = self.client.get(
            reverse("blog:posts-by-category", kwargs={"slug": self.category.slug})
        )
        self.assertContains(tag_response, self.post.title)
        self.assertContains(category_response, self.post.title)
