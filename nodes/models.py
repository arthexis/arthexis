from django.db import models


class Node(models.Model):
    """Information about a running node in the network."""

    hostname = models.CharField(max_length=100)
    address = models.GenericIPAddressField()
    port = models.PositiveIntegerField(default=8000)
    last_seen = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.hostname}:{self.port}"
