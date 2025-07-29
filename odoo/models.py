from django.db import models


class Instance(models.Model):
    """Connection details for an Odoo server."""

    name = models.CharField(max_length=100)
    url = models.URLField()
    database = models.CharField(max_length=100)
    username = models.CharField(max_length=100)
    password = models.CharField(max_length=100)

    def __str__(self) -> str:  # pragma: no cover - simple repr
        return self.name
