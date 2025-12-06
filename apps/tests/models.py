from django.db import models
from django.utils import timezone


class TestResult(models.Model):
    __test__ = False

    class Status(models.TextChoices):
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"
        ERROR = "error", "Error"

    node_id = models.CharField(max_length=512, help_text="Full pytest node identifier")
    name = models.CharField(max_length=255, help_text="Short test name")
    status = models.CharField(max_length=16, choices=Status.choices)
    duration = models.FloatField(null=True, blank=True, help_text="Runtime in seconds")
    log = models.TextField(blank=True, help_text="Captured output and failure details")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "node_id"]
        verbose_name = "Test result"
        verbose_name_plural = "Test results"

    def __str__(self) -> str:
        return f"{self.node_id} ({self.get_status_display()})"
