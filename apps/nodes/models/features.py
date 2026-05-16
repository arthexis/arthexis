from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.db.utils import DatabaseError
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.celery.utils import normalize_periodic_task_name, periodic_task_name_variants
from apps.clocks.utils import has_clock_device
from apps.core.systemctl import _systemctl_command
from apps.emails import mailer
from apps.nodes.feature_detection import node_feature_detection_registry
from apps.nodes.roles import node_feature_allowed_for_node

from .slug_entities import SlugDisplayNaturalKeyMixin, SlugEntityManager

logger = logging.getLogger(__name__)


class NodeFeatureManager(SlugEntityManager):
    pass


@dataclass(frozen=True)
class NodeFeatureDefaultAction:
    label: str
    url_name: str


class NodeFeature(SlugDisplayNaturalKeyMixin, Entity):
    """Feature that may be enabled on nodes and roles."""

    class Footprint(models.TextChoices):
        """Classify how intrusive a node feature is for auto-enable decisions."""

        LIGHT = "light", "Light"
        HEAVY = "heavy", "Heavy"

    slug = models.SlugField(max_length=50, unique=True)
    display = models.CharField(max_length=50)
    footprint = models.CharField(
        max_length=10,
        choices=Footprint.choices,
        default=Footprint.LIGHT,
        help_text=(
            "Classifies whether the feature is lightweight or may modify host "
            "environment configuration."
        ),
    )
    roles = models.ManyToManyField(
        "nodes.NodeRole", blank=True, related_name="features"
    )

    objects = NodeFeatureManager()

    DEFAULT_ACTIONS: dict[str, tuple[NodeFeatureDefaultAction, ...]] = {
        "rfid-scanner": (
            NodeFeatureDefaultAction(
                label=_("Admin Scanner"), url_name="admin:cards_rfid_scan"
            ),
            NodeFeatureDefaultAction(label=_("Public Scanner"), url_name="rfid-reader"),
            NodeFeatureDefaultAction(
                label=_("Public Login"), url_name="pages:rfid-login"
            ),
        ),
        "celery-queue": (
            NodeFeatureDefaultAction(
                label="Celery Report",
                url_name="admin:nodes_nodefeature_celery_report",
            ),
        ),
        "gpio-rtc": (
            NodeFeatureDefaultAction(
                label="Find Clock Devices",
                url_name="admin:clocks_clockdevice_find_devices",
            ),
        ),
        "screenshot-poll": (
            NodeFeatureDefaultAction(
                label="Take Screenshot",
                url_name="admin:nodes_nodefeature_take_screenshot",
            ),
        ),
        "video-cam": (
            NodeFeatureDefaultAction(
                label=_("Discover"),
                url_name="admin:video_videodevice_find_devices",
            ),
            NodeFeatureDefaultAction(
                label="Take Snapshot",
                url_name="admin:video_videodevice_take_snapshot",
            ),
        ),
        "llm-summary": (
            NodeFeatureDefaultAction(
                label=_("Configure"),
                url_name="admin:summary_llmsummaryconfig_wizard",
            ),
        ),
    }

    class Meta:
        ordering = ["display"]
        verbose_name = "Node Feature"
        verbose_name_plural = "Node Features"

    @property
    def is_enabled(self) -> bool:
        """Return whether the feature is enabled for the local node."""
        NodeModel = django_apps.get_model("nodes", "Node")
        if NodeModel is None:
            return False
        node = NodeModel.get_local()
        if not node:
            return False
        if not node_feature_allowed_for_node(self.slug, node):
            return False
        if node.features.filter(pk=self.pk).exists():
            return True
        base_path = node.get_base_path()
        base_dir = Path(settings.BASE_DIR)
        return node._detect_auto_feature(
            self.slug, base_dir=base_dir, base_path=base_path
        )

    def get_default_actions(self) -> tuple[NodeFeatureDefaultAction, ...]:
        """Return the configured default actions for this feature."""

        actions = self.DEFAULT_ACTIONS.get(self.slug, ())
        if isinstance(actions, NodeFeatureDefaultAction):  # pragma: no cover - legacy
            return (actions,)
        return actions

    def get_default_action(self) -> NodeFeatureDefaultAction | None:
        """Return the first configured default action for this feature if any."""

        actions = self.get_default_actions()
        return actions[0] if actions else None


class NodeFeatureAssignment(Entity):
    """Bridge between :class:`Node` and :class:`NodeFeature`."""

    node = models.ForeignKey(
        "nodes.Node", on_delete=models.CASCADE, related_name="feature_assignments"
    )
    feature = models.ForeignKey(
        NodeFeature, on_delete=models.CASCADE, related_name="node_assignments"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("node", "feature")
        verbose_name = "Node Feature Assignment"
        verbose_name_plural = "Node Feature Assignments"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return a readable node-to-feature label."""
        return f"{self.node} -> {self.feature}"

    def save(self, *args, **kwargs):
        """Persist the assignment and resync node feature tasks and service locks."""
        super().save(*args, **kwargs)
        self.node.sync_feature_tasks()
        transaction.on_commit(_reconcile_lifecycle_services)


def _reconcile_lifecycle_services() -> None:
    """Reconcile lifecycle lock and unit records after feature assignment changes."""

    try:
        from apps.services.lifecycle import write_lifecycle_config
    except ImportError:
        logger.debug("Lifecycle reconciliation import failed", exc_info=True)
        return

    write_lifecycle_config()


@receiver(post_delete, sender=NodeFeatureAssignment)
def _sync_tasks_on_assignment_delete(sender, instance, **kwargs):
    """Resync tasks when a feature assignment is removed."""
    node_id = getattr(instance, "node_id", None)
    if not node_id:
        return
    NodeModel = django_apps.get_model("nodes", "Node")
    if NodeModel is None:
        return
    node = NodeModel.objects.filter(pk=node_id).first()
    if node:
        node.sync_feature_tasks()
    transaction.on_commit(_reconcile_lifecycle_services)


class NodeFeatureMixin:
    FEATURE_LOCK_MAP = {
        "rfid-scanner": "rfid-service.lck",
        "celery-queue": "celery.lck",
        "nginx-server": "nginx_mode.lck",
    }
    SYSTEMD_DEPENDENT_FEATURE_SLUGS = frozenset(
        set(FEATURE_LOCK_MAP.keys()) - {"rfid-scanner"}
    )
    CONNECTIVITY_MONITOR_ROLES = {"Control", "Satellite"}
    AUTO_MANAGED_FEATURES = set(FEATURE_LOCK_MAP.keys()) | {
        "gpio-rtc",
        "gui-toast",
        "lcd-screen",
        "llm-summary",
        "playwright-automation",
        "playwright-browser-chromium",
        "playwright-browser-firefox",
        "playwright-browser-webkit",
        "usb-inventory",
        "video-cam",
    }
    LAZY_AUTO_DETECTION_FEATURE_SLUGS = {"rfid-scanner"}
    MANUAL_FEATURE_SLUGS = {"screenshot-poll"}
    ROLE_AUTO_FEATURE_SLUGS: set[str] = set()
    AUTO_ENABLE_FOOTPRINT = NodeFeature.Footprint.LIGHT

    def has_feature(self, slug: str) -> bool:
        """Return whether the node has the requested feature slug."""
        if not node_feature_allowed_for_node(slug, self):
            return False
        return self.features.filter(slug=slug).exists()

    def _apply_role_manual_features(self) -> None:
        """Enable manual features configured as defaults for this node's role."""

        if not self.role_id:
            return

        role_features = self.role.features.filter(
            slug__in=self.MANUAL_FEATURE_SLUGS
        ).values_list("slug", flat=True)
        desired = set(role_features)
        if not desired:
            return

        existing = set(
            self.features.filter(slug__in=desired).values_list("slug", flat=True)
        )
        missing = desired - existing
        if not missing:
            return

        for feature in NodeFeature.objects.filter(slug__in=missing):
            NodeFeatureAssignment.objects.update_or_create(node=self, feature=feature)

    def _apply_role_auto_features(self) -> None:
        """Enable role features that should always be auto-enabled."""

        if not self.role_id:
            return

        role_features = self.role.features.filter(
            slug__in=self.ROLE_AUTO_FEATURE_SLUGS,
            footprint=self.AUTO_ENABLE_FOOTPRINT,
        ).values_list("slug", flat=True)
        desired = set(role_features)
        if not desired:
            return

        existing = set(
            self.features.filter(slug__in=desired).values_list("slug", flat=True)
        )
        missing = desired - existing
        if not missing:
            return

        for feature in NodeFeature.objects.filter(slug__in=missing):
            NodeFeatureAssignment.objects.update_or_create(node=self, feature=feature)

    def _detect_auto_feature(
        self, slug: str, *, base_dir: Path, base_path: Path
    ) -> bool:
        """Detect whether an auto-managed feature is active for the node."""

        if not self._is_auto_detection_allowed_for_slug(slug):
            return False

        hook_result = node_feature_detection_registry.detect(
            slug,
            node=self,
            base_dir=base_dir,
            base_path=base_path,
        )
        if hook_result is None:
            return False
        return bool(hook_result)

    @staticmethod
    def _is_auto_detection_allowed_for_slug(slug: str) -> bool:
        """Return whether auto-detection should run for ``slug``."""

        try:
            Feature = django_apps.get_model("features", "Feature")
        except LookupError:
            return True
        if Feature is None:
            return True

        suite_qs = Feature.objects.filter(node_feature__slug=slug)
        try:
            counts = suite_qs.aggregate(
                total=models.Count("pk"),
                enabled=models.Count("pk", filter=models.Q(is_enabled=True)),
            )
            return counts["total"] == 0 or counts["enabled"] > 0
        except (DatabaseError, RuntimeError):
            return True

    def refresh_features(self):
        """Refresh auto-managed feature assignments and tasks."""
        if not self.pk:
            return
        if not self.is_local:
            self.sync_feature_tasks()
            return
        managed_slugs = (
            self.AUTO_MANAGED_FEATURES - self.LAZY_AUTO_DETECTION_FEATURE_SLUGS
        )
        reconciliation_slugs = self.AUTO_MANAGED_FEATURES
        detected_slugs = set()
        base_path = self.get_base_path()
        base_dir = Path(settings.BASE_DIR)
        for slug in managed_slugs:
            try:
                if self._detect_auto_feature(
                    slug, base_dir=base_dir, base_path=base_path
                ):
                    detected_slugs.add(slug)
            except Exception:
                logger.exception("Automatic detection failed for feature %s", slug)
        current_slugs = set(
            self.features.filter(
                slug__in=reconciliation_slugs,
                footprint=self.AUTO_ENABLE_FOOTPRINT,
            ).values_list("slug", flat=True)
        )
        add_slugs = detected_slugs - current_slugs
        if add_slugs:
            for feature in NodeFeature.objects.filter(
                slug__in=add_slugs,
                footprint=self.AUTO_ENABLE_FOOTPRINT,
            ):
                NodeFeatureAssignment.objects.update_or_create(
                    node=self, feature=feature
                )
        remove_slugs = current_slugs - detected_slugs
        if remove_slugs:
            NodeFeatureAssignment.objects.filter(
                node=self, feature__slug__in=remove_slugs
            ).delete()
        self.sync_feature_tasks()

    def update_manual_features(self, slugs: Iterable[str]):
        """Apply manual feature assignments for the provided slugs."""
        desired = {slug for slug in slugs if slug in self.MANUAL_FEATURE_SLUGS}
        remove_slugs = self.MANUAL_FEATURE_SLUGS - desired
        if remove_slugs:
            NodeFeatureAssignment.objects.filter(
                node=self, feature__slug__in=remove_slugs
            ).delete()
        if desired:
            for feature in NodeFeature.objects.filter(slug__in=desired):
                NodeFeatureAssignment.objects.update_or_create(
                    node=self, feature=feature
                )
        self.sync_feature_tasks()

    def sync_feature_tasks(self):
        """Synchronize periodic tasks based on active features."""
        from apps.features.utils import is_suite_feature_enabled

        screenshot_enabled = self.is_local and is_suite_feature_enabled(
            "screenshot-capture", default=True
        )
        if screenshot_enabled:
            from apps.nodes.feature_checks import feature_checks

            screenshot_feature = NodeFeature.objects.filter(
                slug="screenshot-poll"
            ).first()
            if screenshot_feature is not None:
                screenshot_result = feature_checks.run(screenshot_feature, node=self)
                screenshot_enabled = bool(
                    screenshot_result and screenshot_result.success
                )
            else:
                screenshot_enabled = False
        llm_summary_suite_enabled = is_suite_feature_enabled(
            "llm-summary-suite", default=True
        )
        role_name = getattr(getattr(self, "role", None), "name", None)
        celery_enabled = self.is_local and self.has_feature("celery-queue")
        llm_summary_enabled = (
            llm_summary_suite_enabled
            and celery_enabled
            and role_name == "Control"
            and self.has_feature("llm-summary")
        )
        self._sync_screenshot_task(screenshot_enabled)
        self._sync_landing_lead_task(celery_enabled)
        self._sync_ocpp_session_report_task(celery_enabled)
        self._sync_upstream_poll_task(celery_enabled)
        self._sync_net_message_purge_task(celery_enabled)
        self._sync_node_update_task(celery_enabled)
        self._sync_connectivity_monitor_task(celery_enabled)
        self._sync_llm_summary_task(llm_summary_enabled)

    def _sync_screenshot_task(self, enabled: bool):
        """Sync the periodic screenshot capture task."""
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        raw_task_name = f"capture_screenshot_node_{self.pk}"
        if enabled:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=1, period=IntervalSchedule.MINUTES
            )
            task_name = normalize_periodic_task_name(
                PeriodicTask.objects, raw_task_name
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": "apps.nodes.tasks.capture_node_screenshot",
                    "kwargs": json.dumps(
                        {
                            "url": f"{self.get_preferred_scheme()}://localhost:{self.port}",
                            "port": self.port,
                            "method": "AUTO",
                        }
                    ),
                },
            )
        else:
            PeriodicTask.objects.filter(
                name__in=periodic_task_name_variants(raw_task_name)
            ).delete()

    def _sync_landing_lead_task(self, enabled: bool):
        """Sync the periodic landing lead cleanup task."""
        if not self.is_local:
            return

        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        raw_task_name = "purge_leads"
        task_name = normalize_periodic_task_name(PeriodicTask.objects, raw_task_name)
        if enabled:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="3",
                day_of_week="*",
                day_of_month="*",
                month_of_year="*",
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "crontab": schedule,
                    "interval": None,
                    "task": "apps.sites.tasks.purge_leads",
                    "enabled": True,
                },
            )
        else:
            PeriodicTask.objects.filter(
                name__in=periodic_task_name_variants(raw_task_name)
            ).delete()

    def _sync_ocpp_session_report_task(self, celery_enabled: bool):
        """Sync the periodic OCPP session report task."""
        from django.db.utils import OperationalError, ProgrammingError
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        raw_task_name = "ocpp_send_daily_session_report"
        task_name = normalize_periodic_task_name(PeriodicTask.objects, raw_task_name)

        if not self.is_local:
            return

        if not celery_enabled or not mailer.can_send_email():
            PeriodicTask.objects.filter(
                name__in=periodic_task_name_variants(raw_task_name)
            ).delete()
            return

        try:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="18",
                day_of_week="*",
                day_of_month="*",
                month_of_year="*",
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "crontab": schedule,
                    "interval": None,
                    "task": "apps.ocpp.tasks.send_daily_session_report",
                    "enabled": True,
                },
            )
        except (OperationalError, ProgrammingError):
            logger.debug("Skipping OCPP session report task sync; tables not ready")

    def _sync_upstream_poll_task(self, celery_enabled: bool):
        """Sync the periodic upstream poll task."""
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        raw_task_name = "poll_upstream"
        task_name = normalize_periodic_task_name(PeriodicTask.objects, raw_task_name)
        if celery_enabled:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=5, period=IntervalSchedule.MINUTES
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": "apps.nodes.tasks.poll_upstream",
                    "enabled": True,
                },
            )
        else:
            PeriodicTask.objects.filter(
                name__in=periodic_task_name_variants(raw_task_name)
            ).delete()

    def _sync_net_message_purge_task(self, celery_enabled: bool):
        """Sync the periodic net message purge task."""
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        raw_task_name = "purge_net_messages"
        task_name = normalize_periodic_task_name(PeriodicTask.objects, raw_task_name)

        if celery_enabled:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=12, period=IntervalSchedule.HOURS
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": "apps.nodes.tasks.purge_net_messages",
                    "enabled": True,
                },
            )
        else:
            PeriodicTask.objects.filter(
                name__in=periodic_task_name_variants(raw_task_name)
            ).delete()

    def _sync_node_update_task(self, celery_enabled: bool):
        """Sync the periodic node update task."""
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        raw_task_name = "poll_peers"
        task_name = normalize_periodic_task_name(PeriodicTask.objects, raw_task_name)

        if celery_enabled:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=1,
                period=IntervalSchedule.HOURS,
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "crontab": None,
                    "task": "apps.nodes.tasks.poll_peers",
                    "enabled": True,
                    "one_off": False,
                    "args": "[]",
                    "kwargs": "{}",
                    "description": (
                        "Refreshes node details hourly using the admin Update nodes action."
                    ),
                },
            )
        else:
            PeriodicTask.objects.filter(
                name__in=periodic_task_name_variants(raw_task_name)
            ).update(enabled=False)

    def _sync_connectivity_monitor_task(self, celery_enabled: bool):
        """Sync the periodic connectivity monitor task."""
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        raw_task_name = "monitor_nmcli"
        task_name = normalize_periodic_task_name(PeriodicTask.objects, raw_task_name)

        role_name = getattr(getattr(self, "role", None), "name", None)
        if celery_enabled and role_name in self.CONNECTIVITY_MONITOR_ROLES:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=10, period=IntervalSchedule.MINUTES
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": "apps.nodes.tasks.monitor_nmcli",
                    "enabled": True,
                },
            )
        else:
            PeriodicTask.objects.filter(
                name__in=periodic_task_name_variants(raw_task_name)
            ).delete()

    def _sync_llm_summary_task(self, enabled: bool) -> None:
        """Sync the periodic LLM summary generation task."""
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        from apps.summary.constants import LLM_SUMMARY_CELERY_TASK_NAME

        raw_task_name = "llm_summary_lcd"
        task_name = normalize_periodic_task_name(PeriodicTask.objects, raw_task_name)
        if enabled:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=5, period=IntervalSchedule.MINUTES
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": LLM_SUMMARY_CELERY_TASK_NAME,
                    "enabled": True,
                },
            )
        else:
            PeriodicTask.objects.filter(
                name__in=periodic_task_name_variants(raw_task_name)
            ).delete()
