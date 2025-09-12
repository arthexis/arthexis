from django.db import models
from core.entity import Entity


class UserManual(Entity):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=200)
    languages = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Comma-separated 2-letter language codes",
    )
    content_html = models.TextField()
    content_pdf = models.TextField(help_text="Base64 encoded PDF")

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "User Manual"
        verbose_name_plural = "User Manuals"
