from django.db import models


class NodeRole(models.TextChoices):
    TERMINAL = "terminal", "Terminal"
    SATELLITE = "satellite", "Satellite"
    CONTROL = "control", "Control"
    WATCHTOWER = "watchtower", "Watchtower"


class Node(models.Model):
    identifier = models.SlugField(max_length=80, unique=True)
    display_name = models.CharField(max_length=120)
    role = models.CharField(max_length=20, choices=NodeRole.choices)
    active = models.BooleanField(default=True)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("identifier",)

    def __str__(self):
        return self.display_name


class NodeLink(models.Model):
    source = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="links")
    target = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name="incoming_links"
    )
    relation = models.CharField(max_length=40, default="peer")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("source", "target"), name="unique_node_link"
            )
        ]
