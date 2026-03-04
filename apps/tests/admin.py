"""Admin integration for test-suite tracking models."""

from __future__ import annotations

import subprocess
import sys

from django.conf import settings
from django.contrib import admin, messages
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.discovery.models import DiscoveryItem
from apps.discovery.services import start_discovery
from apps.tests.discovery import TestDiscoveryError, discover_suite_tests
from apps.tests.models import SuiteTest, TestResult


@admin.register(SuiteTest)
class SuiteTestAdmin(admin.ModelAdmin):
    """Admin UI for discovered test metadata and discover refresh action."""

    list_display = (
        "node_id",
        "app_label",
        "module_path",
        "class_name",
        "is_parameterized",
        "updated_at",
    )
    list_filter = ("app_label", "is_parameterized", "updated_at")
    search_fields = ("node_id", "name", "module_path", "class_name", "file_path")
    ordering = ("app_label", "module_path", "class_name", "name", "node_id")
    readonly_fields = ("discovered_at", "updated_at")
    actions = ("discover_tests",)

    @admin.action(description=_("Discover"))
    def discover_tests(self, request, queryset):
        """Load or refresh ``SuiteTest`` records from pytest collection."""

        del queryset
        try:
            tests = discover_suite_tests()
        except TestDiscoveryError as exc:
            self.message_user(
                request,
                _("Unable to discover tests: %(error)s") % {"error": exc},
                level=messages.ERROR,
            )
            return

        discovery = start_discovery(
            _("Discover"),
            request,
            model=SuiteTest,
            metadata={"action": "suite_test_discover"},
        )

        with transaction.atomic():
            deleted_count, _deleted_details = SuiteTest.objects.all().delete()
            SuiteTest.objects.bulk_create([SuiteTest(**item) for item in tests])

            if discovery:
                label_max_len = DiscoveryItem._meta.get_field("label").max_length or 255
                DiscoveryItem.objects.bulk_create(
                    [
                        DiscoveryItem(
                            discovery=discovery,
                            label=str(item["node_id"])[:label_max_len],
                            data={
                                "app_label": item.get("app_label", ""),
                                "marks": item.get("marks", []),
                            },
                        )
                        for item in tests
                    ]
                )

        self.message_user(
            request,
            _("Removed %(count)s existing suite tests.") % {"count": deleted_count},
            level=messages.INFO,
        )
        self.message_user(
            request,
            _("Discovered %(count)s tests from the suite.") % {"count": len(tests)},
            level=messages.SUCCESS,
        )

    discover_tests.requires_queryset = False
    discover_tests.is_discover_action = True


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    list_display = ("node_id", "name", "status", "duration", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("node_id", "name")
    ordering = ("-created_at", "node_id")
    readonly_fields = ("created_at",)
    actions = ("run_all_tests",)

    @admin.action(description=_("Run Suite"))
    def run_all_tests(self, request, queryset):
        """Execute the full test suite and refresh results."""

        del queryset
        deleted_count, _deleted_details = TestResult.objects.all().delete()
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
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            self.message_user(
                request,
                _("Test suite execution timed out after 60 seconds."),
                level=messages.ERROR,
            )
            return
        except OSError as exc:
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
