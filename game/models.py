from django.db import models
from django.utils.translation import gettext_lazy as _


class GamePortal(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    play_url = models.CharField(max_length=200, blank=True)
    download_url = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = _("Game")
        verbose_name_plural = _("Games")
        ordering = ["title"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.title
