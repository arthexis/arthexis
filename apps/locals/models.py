from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class Favorite(Entity):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorites",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    custom_label = models.CharField(max_length=100, blank=True)
    user_data = models.BooleanField(default=False)
    priority = models.IntegerField(default=0)

    class Meta:
        db_table = "pages_favorite"
        unique_together = ("user", "content_type")
        ordering = ["priority", "pk"]
        verbose_name = _("Favorite")
        verbose_name_plural = _("Favorites")
