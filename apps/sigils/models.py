from django.contrib.contenttypes.models import ContentType
from django.db import models


class SigilRoot(models.Model):
    prefix = models.CharField(max_length=80, unique=True)
    context_type = models.CharField(max_length=120)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sigil_roots",
    )
    user_safe = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("prefix",)

    def __str__(self):
        return self.prefix
