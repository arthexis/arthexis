from __future__ import annotations

import re

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from markdown import markdown

from refs.models import Reference


class AuthorProfile(models.Model):
    """Additional author information linked to a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="author_profiles",
    )
    pen_name = models.CharField(max_length=100, blank=True)
    bio = models.TextField(blank=True)
    website = models.URLField(blank=True)

    class Meta:
        ordering = ["pen_name", "id"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.pen_name or self.user.get_username()


class Article(models.Model):
    """Rich text article with optional references and images."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    content = models.TextField()
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    authors = models.ManyToManyField(AuthorProfile, blank=True, related_name="articles")
    references = models.ManyToManyField(Reference, blank=True, related_name="articles")

    class Meta:
        ordering = ["-created"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.title

    def get_absolute_url(self):
        return reverse("arts:article-detail", kwargs={"slug": self.slug})

    IMG_SIGIL_RE = re.compile(r"\[image:([^\]]+)\]")

    def rendered_content(self) -> str:
        """Return the article content with image sigils and markdown rendered."""

        def _replace(match):
            name = match.group(1)
            try:
                img = self.images.get(name=name)
            except MediaImage.DoesNotExist:
                return match.group(0)
            return f'<img src="{img.image.url}" alt="{img.name}">'  # nosec B308

        text = self.IMG_SIGIL_RE.sub(_replace, self.content)
        return markdown(text, extensions=["extra"])


class MediaImage(models.Model):
    """Named image that can be embedded within an article."""

    article = models.ForeignKey(
        Article, on_delete=models.CASCADE, related_name="images"
    )
    name = models.SlugField(max_length=50)
    image = models.ImageField(upload_to="arts/images/")

    class Meta:
        unique_together = ("article", "name")
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name
