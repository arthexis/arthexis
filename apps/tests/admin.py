import subprocess
import sys

from django.conf import settings
from django.contrib import admin, messages
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.discovery.models import DiscoveryItem
from apps.discovery.services import start_discovery
from apps.tests.models import TestResult


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    list_display = ("node_id", "name", "status", "duration", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("node_id", "name")
    ordering = ("-created_at", "node_id")
    readonly_fields = ("created_at",)
    actions = ("discover_tests", "run_all_tests")

    def _collect_test_nodes(self) -> list[str]:
        """Return pytest node identifiers discovered from the test suite."""
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "--collect-only",
                    "-q",
                    "--disable-warnings",
                ],
                cwd=settings.BASE_DIR,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Pytest discovery timed out after 60 seconds in {settings.BASE_DIR}."
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Pytest discovery failed.")

        nodes = []
        for line in result.stdout.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if "::" not in cleaned:
                continue
            nodes.append(cleaned)
        return nodes

    @admin.action(description=_("Discover"))
    def discover_tests(self, request, queryset):
        """Discover all tests in the suite and store them as skipped results."""
        try:
            nodes = self._collect_test_nodes()
        except (RuntimeError, OSError) as exc:
            self.message_user(
                request,
                _("Unable to discover tests: %(error)s") % {"error": exc},
                level=messages.ERROR,
            )
            return

        discovery = start_discovery(
            _("Discover"),
            request,
            model=TestResult,
            metadata={"action": "test_suite_discover"},
        )

        with transaction.atomic():
            deleted_count, _deleted_details = TestResult.objects.all().delete()

            name_max_len = TestResult._meta.get_field("name").max_length or 255
            node_id_max_len = TestResult._meta.get_field("node_id").max_length or 512
            results = [
                TestResult(
                    node_id=node_id[:node_id_max_len],
                    name=node_id.split("::")[-1][:name_max_len],
                    status=TestResult.Status.SKIPPED,
                    duration=None,
                    log="Discovered from pytest collection.",
                )
                for node_id in nodes
            ]
            TestResult.objects.bulk_create(results)

            if discovery:
                label_max_len = (
                    DiscoveryItem._meta.get_field("label").max_length or 255
                )
                discovery_items = [
                    DiscoveryItem(
                        discovery=discovery,
                        label=node_id[:label_max_len],
                        data={"status": TestResult.Status.SKIPPED},
                    )
                    for node_id in nodes
                ]
                DiscoveryItem.objects.bulk_create(discovery_items)

        self.message_user(
            request,
            _("Removed %(count)s existing test results.") % {"count": deleted_count},
            level=messages.INFO,
        )

        self.message_user(
            request,
            _("Discovered %(count)s tests from the suite.") % {"count": len(nodes)},
            level=messages.SUCCESS,
        )

    discover_tests.requires_queryset = False
    discover_tests.is_discover_action = True

    @admin.action(description=_("Run Suite"))
    def run_all_tests(self, request, queryset):
        """Execute the full test suite and refresh results."""
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
