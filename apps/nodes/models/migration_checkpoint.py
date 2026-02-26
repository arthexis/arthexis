"""Models used to track deferred migration transforms in background jobs."""

from django.db import models


class NodeMigrationCheckpoint(models.Model):
    """Persist progress for idempotent node migration transforms."""

    key = models.CharField(max_length=120, unique=True)
    processed_items = models.PositiveIntegerField(default=0)
    total_items = models.PositiveIntegerField(default=0)
    last_pk = models.BigIntegerField(null=True, blank=True)
    is_complete = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Node migration checkpoint"
        verbose_name_plural = "Node migration checkpoints"
        ordering = ("key",)

    def percent_complete(self) -> float:
        """Return completion percent for admin/status visibility."""

        if self.is_complete:
            return 100.0
        if self.total_items <= 0:
            return 0.0
        return min(100.0, round((self.processed_items / self.total_items) * 100, 2))
