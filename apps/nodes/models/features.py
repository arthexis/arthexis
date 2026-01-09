from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
import json
import logging
from pathlib import Path
import shutil
import subprocess
from typing import TYPE_CHECKING

from django.apps import apps as django_apps
from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from apps.audio.utils import has_audio_capture_device
from apps.base.models import Entity
from apps.celery.utils import normalize_periodic_task_name, periodic_task_name_variants
from apps.emails import mailer
from apps.screens.startup_notifications import lcd_feature_enabled_for_paths
from apps.video import has_rpi_camera_stack
from .slug_entities import SlugDisplayNaturalKeyMixin, SlugEntityManager

if TYPE_CHECKING:  # pragma: no cover - used for type checking
    from .node_core import Node


logger = logging.getLogger(__name__)


class NodeFeatureManager(SlugEntityManager):
    pass


@dataclass(frozen=True)
class NodeFeatureDefaultAction:
    label: str
    url_name: str


class NodeFeature(SlugDisplayNaturalKeyMixin, Entity):
    """Feature that may be enabled on nodes and roles."""

    slug = models.SlugField(max_length=50, unique=True)
    display = models.CharField(max_length=50)
    roles = models.ManyToManyField(
        "nodes.NodeRole", blank=True, related_name="features"
    )

    objects = NodeFeatureManager()

    DEFAULT_ACTIONS: dict[str, tuple[NodeFeatureDefaultAction, ...]] = {
        "rfid-scanner": (
            NodeFeatureDefaultAction(
                label="Scan RFIDs", url_name="admin:cards_rfid_scan"
            ),
        ),
        "celery-queue": (
            NodeFeatureDefaultAction(
                label="Celery Report",
                url_name="admin:nodes_nodefeature_celery_report",
            ),
        ),
        "audio-capture": (
            NodeFeatureDefaultAction(
                label="Test Microphone",
                url_name="admin:audio_recordingdevice_test_microphone",
            ),
            NodeFeatureDefaultAction(
                label="Find Recording Devices",
                url_name="admin:audio_recordingdevice_find_devices",
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
        "rpi-camera": (
            NodeFeatureDefaultAction(
                label="Find Video Devices",
                url_name="admin:video_videodevice_find_devices",
            ),
            NodeFeatureDefaultAction(
                label="Take Snapshot",
                url_name="admin:video_videodevice_take_snapshot",
            ),
            NodeFeatureDefaultAction(
                label="Test Camera",
                url_name="admin:video_videodevice_test_camera",
            ),
        ),
    }

    class Meta:
        ordering = ["display"]
        verbose_name = "Node Feature"
        verbose_name_plural = "Node Features"

    @property
    def is_enabled(self) -> bool:
        NodeModel = django_apps.get_model("nodes", "Node")
        if NodeModel is None:
            return False
        node = NodeModel.get_local()
        if not node:
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
        return f"{self.node} -> {self.feature}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.node.sync_feature_tasks()


@receiver(post_delete, sender=NodeFeatureAssignment)
def _sync_tasks_on_assignment_delete(sender, instance, **kwargs):
    node_id = getattr(instance, "node_id", None)
    if not node_id:
        return
    NodeModel = django_apps.get_model("nodes", "Node")
    if NodeModel is None:
        return
    node = NodeModel.objects.filter(pk=node_id).first()
    if node:
        node.sync_feature_tasks()


class NodeFeatureMixin:
    FEATURE_LOCK_MAP = {
        "rfid-scanner": "rfid.lck",
        "celery-queue": "celery.lck",
        "nginx-server": "nginx_mode.lck",
    }
    CONNECTIVITY_MONITOR_ROLES = {"Control", "Satellite"}
    AP_ROUTER_SSID = "gelectriic-ap"
    NMCLI_TIMEOUT = 5
    AUTO_MANAGED_FEATURES = set(FEATURE_LOCK_MAP.keys()) | {
        "lcd-screen",
        "gui-toast",
        "rpi-camera",
        "ap-router",
        "llm-summary",
    }
    MANUAL_FEATURE_SLUGS = {"screenshot-poll", "audio-capture"}

    def has_feature(self, slug: str) -> bool:
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
            NodeFeatureAssignment.objects.update_or_create(
                node=self, feature=feature
            )

    @classmethod
    def _has_rpi_camera(cls) -> bool:
        """Return ``True`` when the Raspberry Pi camera stack is available."""

        return has_rpi_camera_stack()

    @classmethod
    def _has_audio_capture_device(cls) -> bool:
        """Return ``True`` when an audio capture device is available."""

        return has_audio_capture_device()

    @classmethod
    def _hosts_gelectriic_ap(cls) -> bool:
        """Return ``True`` when the node is hosting the gelectriic access point."""

        nmcli_path = shutil.which("nmcli")
        if not nmcli_path:
            return False
        try:
            result = subprocess.run(
                [
                    nmcli_path,
                    "-t",
                    "-f",
                    "NAME,DEVICE,TYPE",
                    "connection",
                    "show",
                    "--active",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=cls.NMCLI_TIMEOUT,
            )
        except Exception:
            return False
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            if not line:
                continue
            parts = line.split(":", 2)
            if not parts:
                continue
            name = parts[0]
            conn_type = ""
            if len(parts) == 3:
                conn_type = parts[2]
            elif len(parts) > 1:
                conn_type = parts[1]
            if name != cls.AP_ROUTER_SSID:
                continue
            conn_type_normalized = conn_type.strip().lower()
            if conn_type_normalized not in {"wifi", "802-11-wireless"}:
                continue
            try:
                mode_result = subprocess.run(
                    [
                        nmcli_path,
                        "-g",
                        "802-11-wireless.mode",
                        "connection",
                        "show",
                        name,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=cls.NMCLI_TIMEOUT,
                )
            except Exception:
                continue
            if mode_result.returncode != 0:
                continue
            if mode_result.stdout.strip() == "ap":
                return True
        return False

    def _detect_auto_feature(
        self, slug: str, *, base_dir: Path, base_path: Path
    ) -> bool:
        lock = self.FEATURE_LOCK_MAP.get(slug)
        if lock:
            project_lock_dir = base_dir / ".locks"
            lock_dirs = [base_path / ".locks"]
            if project_lock_dir not in lock_dirs:
                lock_dirs.append(project_lock_dir)
            return any((lock_dir / lock).exists() for lock_dir in lock_dirs)
        if slug == "lcd-screen":
            return lcd_feature_enabled_for_paths(
                base_dir=base_dir, node_base_path=base_path
            )
        if slug == "gui-toast":
            try:
                from apps.core.notifications import supports_gui_toast
            except Exception:
                return False
            try:
                return supports_gui_toast()
            except Exception:
                logger.exception("GUI toast detection failed")
                return False
        if slug == "rpi-camera":
            return self._has_rpi_camera()
        if slug == "ap-router":
            return self._hosts_gelectriic_ap()
        return False

    def refresh_features(self):
        if not self.pk:
            return
        if not self.is_local:
            self.sync_feature_tasks()
            return
        detected_slugs = set()
        base_path = self.get_base_path()
        base_dir = Path(settings.BASE_DIR)
        for slug in self.AUTO_MANAGED_FEATURES:
            try:
                if self._detect_auto_feature(slug, base_dir=base_dir, base_path=base_path):
                    detected_slugs.add(slug)
            except Exception:
                logger.exception("Automatic detection failed for feature %s", slug)
        current_slugs = set(
            self.features.filter(slug__in=self.AUTO_MANAGED_FEATURES).values_list(
                "slug", flat=True
            )
        )
        add_slugs = detected_slugs - current_slugs
        if add_slugs:
            for feature in NodeFeature.objects.filter(slug__in=add_slugs):
                NodeFeatureAssignment.objects.update_or_create(
                    node=self, feature=feature
                )
        remove_slugs = current_slugs - detected_slugs
        if remove_slugs:
            NodeFeatureAssignment.objects.filter(
                node=self, feature__slug__in=remove_slugs
            ).delete()
        self.sync_feature_tasks()

    def update_manual_features(
        self, slugs: Iterable[str]
    ):
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
        screenshot_enabled = self.has_feature("screenshot-poll")
        celery_enabled = self.is_local and self.has_feature("celery-queue")
        llm_summary_enabled = celery_enabled and self.has_feature("llm-summary")
        self._sync_screenshot_task(screenshot_enabled)
        self._sync_landing_lead_task(celery_enabled)
        self._sync_ocpp_session_report_task(celery_enabled)
        self._sync_upstream_poll_task(celery_enabled)
        self._sync_net_message_purge_task(celery_enabled)
        self._sync_node_update_task(celery_enabled)
        self._sync_connectivity_monitor_task(celery_enabled)
        self._sync_llm_summary_task(llm_summary_enabled)

    def _sync_screenshot_task(self, enabled: bool):
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
        if not self.is_local:
            return

        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        raw_task_name = "purge_leads"
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, raw_task_name
        )
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
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
        from django.db.utils import OperationalError, ProgrammingError

        raw_task_name = "ocpp_send_daily_session_report"
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, raw_task_name
        )

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
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        raw_task_name = "poll_upstream"
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, raw_task_name
        )
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
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        raw_task_name = "purge_net_messages"
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, raw_task_name
        )

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
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        raw_task_name = "poll_peers"
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, raw_task_name
        )

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
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        raw_task_name = "monitor_nmcli"
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, raw_task_name
        )

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
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        raw_task_name = "llm_summary_lcd"
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, raw_task_name
        )
        if enabled:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=5, period=IntervalSchedule.MINUTES
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": "apps.summary.tasks.generate_lcd_log_summary",
                    "enabled": True,
                },
            )
        else:
            PeriodicTask.objects.filter(
                name__in=periodic_task_name_variants(raw_task_name)
            ).delete()
