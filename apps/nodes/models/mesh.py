from django.db import models


class MeshNodeModel(models.Model):
    """Abstract base for mesh-oriented models tied to a canonical node identity."""

    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
    )

    class Meta:
        abstract = True
