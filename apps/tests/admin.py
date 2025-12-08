import subprocess
import sys

from django.conf import settings
from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.tests.models import TestResult


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    list_display = ("node_id", "name", "status", "duration", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("node_id", "name")
    ordering = ("-created_at", "node_id")
    readonly_fields = ("created_at",)
    actions = ["run_all_tests"]

    @admin.action(description=_("Run All Tests"))
    def run_all_tests(self, request, queryset):
        deleted_count, _ = TestResult.objects.all().delete()
        self.message_user(
            request,
            _("Removed %(count)s existing test results.") % {"count": deleted_count},
            level=messages.INFO,
        )

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest"],
                cwd=settings.BASE_DIR,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as exc:
            self.message_user(
                request,
                _("Unable to execute the test suite: %(error)s") % {"error": exc},
                level=messages.ERROR,
            )
            return

        if result.returncode == 0:
            level = messages.SUCCESS
            message = _("Test suite completed successfully and results were refreshed.")
        else:
            level = messages.ERROR
            message = _(
                "Test suite finished with errors (exit code %(code)s). Check logs for details."
            ) % {"code": result.returncode}

        self.message_user(request, message, level=level)
