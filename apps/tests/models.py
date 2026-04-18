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


class SuiteTest(models.Model):
    """Metadata for a discovered pytest test in the local suite."""

    node_id = models.CharField(
        max_length=512,
        unique=True,
        help_text="Full pytest node identifier.",
    )
    name = models.CharField(max_length=255, help_text="Short test name.")
    module_path = models.CharField(
        max_length=512,
        blank=True,
        help_text="Python module path that owns the test.",
    )
    app_label = models.CharField(
        max_length=128,
        blank=True,
        help_text="Inferred app label from file path.",
    )
    class_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Owning test class when applicable.",
    )
    marks = models.JSONField(
        default=list,
        blank=True,
        help_text="Pytest marks/keywords attached to the test.",
    )
    file_path = models.CharField(
        max_length=512,
        blank=True,
        help_text="Repository-relative python file path.",
    )
    is_parameterized = models.BooleanField(
        default=False,
        help_text="Whether this test node includes parametrization.",
    )
    discovered_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["app_label", "module_path", "class_name", "name", "node_id"]
        verbose_name = "Suite test"
        verbose_name_plural = "Suite Tests"

    def __str__(self) -> str:
        """Return a readable test identity for admin views."""

        return self.node_id
