from django.db import models


class Node(models.Model):
    """Information about a running node in the network."""

    hostname = models.CharField(max_length=100)
    address = models.GenericIPAddressField()
    port = models.PositiveIntegerField(default=8000)
    badge_color = models.CharField(max_length=7, default="#28a745")
    last_seen = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.hostname}:{self.port}"


class NodeScreenshot(models.Model):
    """Screenshot captured from a node."""

    node = models.ForeignKey(
        Node, on_delete=models.SET_NULL, null=True, blank=True
    )
    path = models.CharField(max_length=255)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.path
