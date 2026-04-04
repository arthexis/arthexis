"""Sample models exposed by the reference plugin."""

from django.db import models


class SampleConnector(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=128)

    class Meta:
        verbose_name = "Sample connector"
        verbose_name_plural = "Sample connectors"

    def __str__(self) -> str:
        return self.title
