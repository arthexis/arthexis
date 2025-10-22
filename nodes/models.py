from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from django.db import models
from django.db.utils import DatabaseError
from django.db.models.signals import post_delete
from django.dispatch import Signal, receiver
from core.entity import Entity
from core.models import PackageRelease, Profile
from core.fields import SigilLongAutoField, SigilShortAutoField
import re
import json
import base64
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from datetime import timedelta
import uuid
import os
import shutil
import socket
import stat
import subprocess
from pathlib import Path
from utils import revision
from core.notifications import notify_async
from django.core.exceptions import ValidationError
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.mail import get_connection
from django.core.serializers.base import DeserializationError
from core import mailer
import logging


logger = logging.getLogger(__name__)


ROLE_RENAMES: dict[str, str] = {"Constellation": "Watchtower"}


class NodeRoleManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class NodeRole(Entity):
    """Assignable role for a :class:`Node`."""

    name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=200, blank=True)

    objects = NodeRoleManager()

    class Meta:
        ordering = ["name"]
        verbose_name = "Node Role"
        verbose_name_plural = "Node Roles"

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class NodeFeatureManager(models.Manager):
    def get_by_natural_key(self, slug: str):
        return self.get(slug=slug)


@dataclass(frozen=True)
class NodeFeatureDefaultAction:
    label: str
    url_name: str


class NodeFeature(Entity):
    """Feature that may be enabled on nodes and roles."""

    slug = models.SlugField(max_length=50, unique=True)
    display = models.CharField(max_length=50)
    roles = models.ManyToManyField(NodeRole, blank=True, related_name="features")

    objects = NodeFeatureManager()

    DEFAULT_ACTIONS: dict[str, tuple[NodeFeatureDefaultAction, ...]] = {
        "rfid-scanner": (
            NodeFeatureDefaultAction(
                label="Scan RFIDs", url_name="admin:core_rfid_scan"
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
                label="View Waveform",
                url_name="admin:nodes_nodefeature_view_waveform",
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
                label="Take a Snapshot",
                url_name="admin:nodes_nodefeature_take_snapshot",
            ),
            NodeFeatureDefaultAction(
                label="View stream",
                url_name="admin:nodes_nodefeature_view_stream",
            ),
        ),
    }

    class Meta:
        ordering = ["display"]
        verbose_name = "Node Feature"
        verbose_name_plural = "Node Features"

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.slug,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display

    @property
    def is_enabled(self) -> bool:
        from django.conf import settings
        from pathlib import Path

        node = Node.get_local()
        if not node:
            return False
        if node.features.filter(pk=self.pk).exists():
            return True
        if self.slug == "gway-runner":
            return Node._has_gway_runner()
        if self.slug == "gui-toast":
            from core.notifications import supports_gui_toast

            return supports_gui_toast()
        if self.slug == "rpi-camera":
            return Node._has_rpi_camera()
        lock_map = {
            "lcd-screen": "lcd_screen.lck",
            "rfid-scanner": "rfid.lck",
            "celery-queue": "celery.lck",
            "nginx-server": "nginx_mode.lck",
        }
        lock = lock_map.get(self.slug)
        if lock:
            base_path = Path(node.base_path or settings.BASE_DIR)
            return (base_path / "locks" / lock).exists()
        return False

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


def get_terminal_role():
    """Return the NodeRole representing a Terminal if it exists."""
    return NodeRole.objects.filter(name="Terminal").first()


class Node(Entity):
    """Information about a running node in the network."""

    DEFAULT_BADGE_COLOR = "#28a745"
    ROLE_BADGE_COLORS = {
        "Watchtower": "#daa520",  # goldenrod
        "Constellation": "#daa520",  # legacy alias
        "Control": "#673ab7",  # deep purple
        "Interface": "#0dcaf0",  # cyan
    }

    class Relation(models.TextChoices):
        UPSTREAM = "UPSTREAM", "Upstream"
        DOWNSTREAM = "DOWNSTREAM", "Downstream"
        PEER = "PEER", "Peer"
        SELF = "SELF", "Self"

    hostname = models.CharField(max_length=100)
    address = models.GenericIPAddressField()
    mac_address = models.CharField(max_length=17, unique=True, null=True, blank=True)
    port = models.PositiveIntegerField(default=8000)
    message_queue_length = models.PositiveSmallIntegerField(
        default=10,
        help_text="Maximum queued NetMessages to retain for this peer.",
    )
    badge_color = models.CharField(max_length=7, default=DEFAULT_BADGE_COLOR)
    role = models.ForeignKey(NodeRole, on_delete=models.SET_NULL, null=True, blank=True)
    current_relation = models.CharField(
        max_length=10,
        choices=Relation.choices,
        default=Relation.PEER,
    )
    last_seen = models.DateTimeField(auto_now=True)
    enable_public_api = models.BooleanField(
        default=False,
        verbose_name="enable public API",
    )
    public_endpoint = models.SlugField(blank=True, unique=True)
    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="UUID",
    )
    public_key = models.TextField(blank=True)
    base_path = models.CharField(max_length=255, blank=True)
    installed_version = models.CharField(max_length=20, blank=True)
    installed_revision = models.CharField(max_length=40, blank=True)
    features = models.ManyToManyField(
        NodeFeature,
        through="NodeFeatureAssignment",
        related_name="nodes",
        blank=True,
    )

    FEATURE_LOCK_MAP = {
        "lcd-screen": "lcd_screen.lck",
        "rfid-scanner": "rfid.lck",
        "celery-queue": "celery.lck",
        "nginx-server": "nginx_mode.lck",
    }
    RPI_CAMERA_DEVICE = Path("/dev/video0")
    RPI_CAMERA_BINARIES = ("rpicam-hello", "rpicam-still", "rpicam-vid")
    AP_ROUTER_SSID = "gelectriic-ap"
    NMCLI_TIMEOUT = 5
    GWAY_RUNNER_COMMAND = "gway"
    GWAY_RUNNER_CANDIDATES = ("~/.local/bin/gway", "/usr/local/bin/gway")
    AUTO_MANAGED_FEATURES = set(FEATURE_LOCK_MAP.keys()) | {
        "gui-toast",
        "rpi-camera",
        "ap-router",
        "gway-runner",
    }
    MANUAL_FEATURE_SLUGS = {"clipboard-poll", "screenshot-poll", "audio-capture"}

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.hostname}:{self.port}"

    @staticmethod
    def get_current_mac() -> str:
        """Return the MAC address of the current host."""
        return ":".join(re.findall("..", f"{uuid.getnode():012x}"))

    @classmethod
    def normalize_relation(cls, value):
        """Normalize ``value`` to a valid :class:`Relation`."""

        if isinstance(value, cls.Relation):
            return value
        if value is None:
            return cls.Relation.PEER
        text = str(value).strip()
        if not text:
            return cls.Relation.PEER
        for relation in cls.Relation:
            if text.lower() == relation.label.lower():
                return relation
            if text.upper() == relation.name:
                return relation
            if text.lower() == relation.value.lower():
                return relation
        return cls.Relation.PEER

    @classmethod
    def get_local(cls):
        """Return the node representing the current host if it exists."""
        mac = cls.get_current_mac()
        try:
            return cls.objects.filter(mac_address=mac).first()
        except DatabaseError:
            logger.debug("nodes.Node.get_local skipped: database unavailable", exc_info=True)
            return None

    @classmethod
    def register_current(cls):
        """Create or update the :class:`Node` entry for this host."""
        hostname_override = (
            os.environ.get("NODE_HOSTNAME")
            or os.environ.get("HOSTNAME")
            or ""
        )
        hostname_override = hostname_override.strip()
        hostname = hostname_override or socket.gethostname()
        try:
            address = socket.gethostbyname(hostname)
        except OSError:
            address = "127.0.0.1"
        port = int(os.environ.get("PORT", 8000))
        base_path = str(settings.BASE_DIR)
        ver_path = Path(settings.BASE_DIR) / "VERSION"
        installed_version = ver_path.read_text().strip() if ver_path.exists() else ""
        rev_value = revision.get_revision()
        installed_revision = rev_value if rev_value else ""
        mac = cls.get_current_mac()
        endpoint_override = os.environ.get("NODE_PUBLIC_ENDPOINT", "").strip()
        slug_source = endpoint_override or hostname
        slug = slugify(slug_source)
        if not slug:
            slug = cls._generate_unique_public_endpoint(hostname or mac)
        node = cls.objects.filter(mac_address=mac).first()
        if not node:
            node = cls.objects.filter(public_endpoint=slug).first()
        defaults = {
            "hostname": hostname,
            "address": address,
            "port": port,
            "base_path": base_path,
            "installed_version": installed_version,
            "installed_revision": installed_revision,
            "public_endpoint": slug,
            "mac_address": mac,
            "current_relation": cls.Relation.SELF,
        }
        role_lock = Path(settings.BASE_DIR) / "locks" / "role.lck"
        role_name = role_lock.read_text().strip() if role_lock.exists() else "Terminal"
        role_name = ROLE_RENAMES.get(role_name, role_name)
        desired_role = NodeRole.objects.filter(name=role_name).first()

        if node:
            update_fields = []
            for field, value in defaults.items():
                if getattr(node, field) != value:
                    setattr(node, field, value)
                    update_fields.append(field)
            if desired_role and node.role_id != desired_role.id:
                node.role = desired_role
                update_fields.append("role")
            if update_fields:
                node.save(update_fields=update_fields)
            else:
                node.refresh_features()
            created = False
        else:
            node = cls.objects.create(**defaults)
            created = True
            if desired_role:
                node.role = desired_role
                node.save(update_fields=["role"])
        if created and node.role is None:
            terminal = NodeRole.objects.filter(name="Terminal").first()
            if terminal:
                node.role = terminal
                node.save(update_fields=["role"])
        node.ensure_keys()
        node.notify_peers_of_update()
        return node, created

    def notify_peers_of_update(self):
        """Attempt to update this node's registration with known peers."""

        from secrets import token_hex

        try:
            import requests
        except Exception:  # pragma: no cover - requests should be available
            return

        security_dir = Path(self.base_path or settings.BASE_DIR) / "security"
        priv_path = security_dir / f"{self.public_endpoint}"
        if not priv_path.exists():
            logger.debug("Private key for %s not found; skipping peer update", self)
            return
        try:
            private_key = serialization.load_pem_private_key(
                priv_path.read_bytes(), password=None
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load private key for %s: %s", self, exc)
            return
        token = token_hex(16)
        try:
            signature = private_key.sign(
                token.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to sign peer update for %s: %s", self, exc)
            return

        payload = {
            "hostname": self.hostname,
            "address": self.address,
            "port": self.port,
            "mac_address": self.mac_address,
            "public_key": self.public_key,
            "token": token,
            "signature": base64.b64encode(signature).decode(),
        }
        if self.installed_version:
            payload["installed_version"] = self.installed_version
        if self.installed_revision:
            payload["installed_revision"] = self.installed_revision

        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        headers = {"Content-Type": "application/json"}

        peers = Node.objects.exclude(pk=self.pk)
        for peer in peers:
            host_candidates: list[str] = []
            if peer.address:
                host_candidates.append(peer.address)
            if peer.hostname and peer.hostname not in host_candidates:
                host_candidates.append(peer.hostname)
            port = peer.port or 8000
            urls: list[str] = []
            for host in host_candidates:
                host = host.strip()
                if not host:
                    continue
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                http_url = (
                    f"http://{host}/nodes/register/"
                    if port == 80
                    else f"http://{host}:{port}/nodes/register/"
                )
                https_url = (
                    f"https://{host}/nodes/register/"
                    if port in {80, 443}
                    else f"https://{host}:{port}/nodes/register/"
                )
                for url in (https_url, http_url):
                    if url not in urls:
                        urls.append(url)
            if not urls:
                continue
            for url in urls:
                try:
                    response = requests.post(
                        url, data=payload_json, headers=headers, timeout=2
                    )
                except Exception as exc:  # pragma: no cover - best effort
                    logger.debug("Failed to update %s via %s: %s", peer, url, exc)
                    continue
                if response.ok:
                    version_display = _format_upgrade_body(
                        self.installed_version,
                        self.installed_revision,
                    )
                    version_suffix = f" ({version_display})" if version_display else ""
                    logger.info(
                        "Announced startup to %s%s",
                        peer,
                        version_suffix,
                    )
                    break
            else:
                logger.warning("Unable to notify node %s of startup", peer)

    def ensure_keys(self):
        security_dir = Path(settings.BASE_DIR) / "security"
        security_dir.mkdir(parents=True, exist_ok=True)
        priv_path = security_dir / f"{self.public_endpoint}"
        pub_path = security_dir / f"{self.public_endpoint}.pub"
        if not priv_path.exists() or not pub_path.exists():
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            private_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
            public_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            priv_path.write_bytes(private_bytes)
            pub_path.write_bytes(public_bytes)
            self.public_key = public_bytes.decode()
            self.save(update_fields=["public_key"])
        elif not self.public_key:
            self.public_key = pub_path.read_text()
            self.save(update_fields=["public_key"])

    def get_private_key(self):
        """Return the private key for this node if available."""

        if not self.public_endpoint:
            return None
        try:
            self.ensure_keys()
        except Exception:
            return None
        priv_path = (
            Path(self.base_path or settings.BASE_DIR)
            / "security"
            / f"{self.public_endpoint}"
        )
        try:
            return serialization.load_pem_private_key(
                priv_path.read_bytes(), password=None
            )
        except Exception:
            return None

    @property
    def is_local(self):
        """Determine if this node represents the current host."""
        return self.mac_address == self.get_current_mac()

    @classmethod
    def _generate_unique_public_endpoint(
        cls, value: str | None, *, exclude_pk: int | None = None
    ) -> str:
        """Return a unique public endpoint slug for ``value``."""

        field = cls._meta.get_field("public_endpoint")
        max_length = getattr(field, "max_length", None) or 50
        base_slug = slugify(value or "") or "node"
        if len(base_slug) > max_length:
            base_slug = base_slug[:max_length]
        slug = base_slug
        queryset = cls.objects.all()
        if exclude_pk is not None:
            queryset = queryset.exclude(pk=exclude_pk)
        counter = 2
        while queryset.filter(public_endpoint=slug).exists():
            suffix = f"-{counter}"
            available = max_length - len(suffix)
            if available <= 0:
                slug = suffix[-max_length:]
            else:
                slug = f"{base_slug[:available]}{suffix}"
            counter += 1
        return slug

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")

        def include_update_field(field: str):
            nonlocal update_fields
            if update_fields is None:
                return
            fields = set(update_fields)
            if field in fields:
                return
            fields.add(field)
            update_fields = tuple(fields)
            kwargs["update_fields"] = update_fields

        role_name = None
        role = getattr(self, "role", None)
        if role and getattr(role, "name", None):
            role_name = role.name
        elif self.role_id:
            role_name = (
                NodeRole.objects.filter(pk=self.role_id)
                .values_list("name", flat=True)
                .first()
            )

        role_color = self.ROLE_BADGE_COLORS.get(role_name)
        if role_color and (
            not self.badge_color or self.badge_color == self.DEFAULT_BADGE_COLOR
        ):
            self.badge_color = role_color
            include_update_field("badge_color")

        if self.mac_address:
            self.mac_address = self.mac_address.lower()
        endpoint_value = slugify(self.public_endpoint or "")
        if not endpoint_value:
            endpoint_value = self._generate_unique_public_endpoint(
                self.hostname, exclude_pk=self.pk
            )
        else:
            queryset = (
                self.__class__.objects.exclude(pk=self.pk)
                if self.pk
                else self.__class__.objects.all()
            )
            if queryset.filter(public_endpoint=endpoint_value).exists():
                endpoint_value = self._generate_unique_public_endpoint(
                    self.hostname or endpoint_value, exclude_pk=self.pk
                )
        if self.public_endpoint != endpoint_value:
            self.public_endpoint = endpoint_value
            include_update_field("public_endpoint")
        super().save(*args, **kwargs)
        if self.pk:
            self.refresh_features()

    def has_feature(self, slug: str) -> bool:
        return self.features.filter(slug=slug).exists()

    @classmethod
    def _has_rpi_camera(cls) -> bool:
        """Return ``True`` when the Raspberry Pi camera stack is available."""

        device = cls.RPI_CAMERA_DEVICE
        if not device.exists():
            return False
        device_path = str(device)
        try:
            mode = os.stat(device_path).st_mode
        except OSError:
            return False
        if not stat.S_ISCHR(mode):
            return False
        if not os.access(device_path, os.R_OK | os.W_OK):
            return False
        for binary in cls.RPI_CAMERA_BINARIES:
            tool_path = shutil.which(binary)
            if not tool_path:
                return False
            try:
                result = subprocess.run(
                    [tool_path, "--help"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
            except Exception:
                return False
            if result.returncode != 0:
                return False
        return True

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

    @classmethod
    def _find_gway_runner_command(cls) -> str | None:
        command = shutil.which(cls.GWAY_RUNNER_COMMAND)
        if command:
            return command
        for candidate in cls.GWAY_RUNNER_CANDIDATES:
            expanded = os.path.expanduser(candidate)
            if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
                return expanded
        return None

    @classmethod
    def _has_gway_runner(cls) -> bool:
        return cls._find_gway_runner_command() is not None

    def refresh_features(self):
        if not self.pk:
            return
        if not self.is_local:
            self.sync_feature_tasks()
            return
        detected_slugs = set()
        base_path = Path(self.base_path or settings.BASE_DIR)
        locks_dir = base_path / "locks"
        for slug, filename in self.FEATURE_LOCK_MAP.items():
            if (locks_dir / filename).exists():
                detected_slugs.add(slug)
        if self._has_rpi_camera():
            detected_slugs.add("rpi-camera")
        if self._has_gway_runner():
            detected_slugs.add("gway-runner")
        if self._hosts_gelectriic_ap():
            detected_slugs.add("ap-router")
        try:
            from core.notifications import supports_gui_toast
        except Exception:
            pass
        else:
            try:
                if supports_gui_toast():
                    detected_slugs.add("gui-toast")
            except Exception:
                pass
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

    def update_manual_features(self, slugs: Iterable[str]):
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
        clipboard_enabled = self.has_feature("clipboard-poll")
        screenshot_enabled = self.has_feature("screenshot-poll")
        celery_enabled = self.is_local and self.has_feature("celery-queue")
        self._sync_clipboard_task(clipboard_enabled)
        self._sync_screenshot_task(screenshot_enabled)
        self._sync_landing_lead_task(celery_enabled)
        self._sync_ocpp_session_report_task(celery_enabled)
        self._sync_upstream_poll_task(celery_enabled)

    def _sync_clipboard_task(self, enabled: bool):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        task_name = f"poll_clipboard_node_{self.pk}"
        if enabled:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=5, period=IntervalSchedule.SECONDS
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": "nodes.tasks.sample_clipboard",
                },
            )
        else:
            PeriodicTask.objects.filter(name=task_name).delete()

    def _sync_screenshot_task(self, enabled: bool):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
        import json

        task_name = f"capture_screenshot_node_{self.pk}"
        if enabled:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=1, period=IntervalSchedule.MINUTES
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": "nodes.tasks.capture_node_screenshot",
                    "kwargs": json.dumps(
                        {
                            "url": f"http://localhost:{self.port}",
                            "port": self.port,
                            "method": "AUTO",
                        }
                    ),
                },
            )
        else:
            PeriodicTask.objects.filter(name=task_name).delete()

    def _sync_landing_lead_task(self, enabled: bool):
        if not self.is_local:
            return

        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        task_name = "pages_purge_landing_leads"
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
                    "task": "pages.tasks.purge_expired_landing_leads",
                    "enabled": True,
                },
            )
        else:
            PeriodicTask.objects.filter(name=task_name).delete()

    def _sync_ocpp_session_report_task(self, celery_enabled: bool):
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
        from django.db.utils import OperationalError, ProgrammingError

        task_name = "ocpp_send_daily_session_report"

        if not self.is_local:
            return

        if not celery_enabled or not mailer.can_send_email():
            PeriodicTask.objects.filter(name=task_name).delete()
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
                    "task": "ocpp.tasks.send_daily_session_report",
                    "enabled": True,
                },
            )
        except (OperationalError, ProgrammingError):
            logger.debug("Skipping OCPP session report task sync; tables not ready")

    def _sync_upstream_poll_task(self, celery_enabled: bool):
        if not self.is_local:
            return

        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        task_name = "nodes_poll_upstream_messages"
        if celery_enabled:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=1, period=IntervalSchedule.MINUTES
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": "nodes.tasks.poll_unreachable_upstream",
                    "enabled": True,
                },
            )
        else:
            PeriodicTask.objects.filter(name=task_name).delete()

    def send_mail(
        self,
        subject: str,
        message: str,
        recipient_list: list[str],
        from_email: str | None = None,
        **kwargs,
    ):
        """Send an email using this node's configured outbox if available."""
        outbox = getattr(self, "email_outbox", None)
        logger.info(
            "Node %s queueing email to %s using %s backend",
            self.pk,
            recipient_list,
            "outbox" if outbox else "default",
        )
        return mailer.send(
            subject,
            message,
            recipient_list,
            from_email,
            outbox=outbox,
            **kwargs,
        )


node_information_updated = Signal()


def _format_upgrade_body(version: str, revision: str) -> str:
    version = (version or "").strip()
    revision = (revision or "").strip()
    parts: list[str] = []
    if version:
        normalized = version.lstrip("vV") or version
        base_version = normalized.rstrip("+")
        display_version = normalized
        if (
            base_version
            and revision
            and not PackageRelease.matches_revision(base_version, revision)
            and not normalized.endswith("+")
        ):
            display_version = f"{display_version}+"
        parts.append(f"v{display_version}")
    if revision:
        rev_clean = re.sub(r"[^0-9A-Za-z]", "", revision)
        rev_short = (rev_clean[-6:] if rev_clean else revision[-6:])
        parts.append(f"r{rev_short}")
    return " ".join(parts).strip()


@receiver(node_information_updated)
def _announce_peer_startup(
    sender,
    *,
    node: "Node",
    previous_version: str = "",
    previous_revision: str = "",
    current_version: str = "",
    current_revision: str = "",
    **_: object,
) -> None:
    current_version = (current_version or "").strip()
    current_revision = (current_revision or "").strip()
    previous_version = (previous_version or "").strip()
    previous_revision = (previous_revision or "").strip()

    local = Node.get_local()
    if local and node.pk == local.pk:
        return

    body = _format_upgrade_body(current_version, current_revision)
    if not body:
        body = "Online"

    hostname = (node.hostname or "Node").strip() or "Node"
    subject = f"UP {hostname}"
    notify_async(subject, body)


class NodeFeatureAssignment(Entity):
    """Bridge between :class:`Node` and :class:`NodeFeature`."""

    node = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name="feature_assignments"
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
    node = Node.objects.filter(pk=node_id).first()
    if node:
        node.sync_feature_tasks()


class NodeManager(Profile):
    """Credentials for interacting with external DNS providers."""

    class Provider(models.TextChoices):
        GODADDY = "godaddy", "GoDaddy"

    profile_fields = (
        "provider",
        "api_key",
        "api_secret",
        "customer_id",
        "default_domain",
    )

    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.GODADDY,
    )
    api_key = SigilShortAutoField(
        max_length=255,
        verbose_name="API key",
        help_text="API key issued by the DNS provider.",
    )
    api_secret = SigilShortAutoField(
        max_length=255,
        verbose_name="API secret",
        help_text="API secret issued by the DNS provider.",
    )
    customer_id = SigilShortAutoField(
        max_length=100,
        blank=True,
        verbose_name="Customer ID",
        help_text="Optional GoDaddy customer identifier for the account.",
    )
    default_domain = SigilShortAutoField(
        max_length=253,
        blank=True,
        help_text="Fallback domain when records omit one.",
    )
    use_sandbox = models.BooleanField(
        default=False,
        help_text="Use the GoDaddy OTE (test) environment.",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Disable to prevent deployments with this manager.",
    )

    class Meta:
        verbose_name = "Node Manager"
        verbose_name_plural = "Node Managers"

    def __str__(self) -> str:
        owner = self.owner_display()
        provider = self.get_provider_display()
        if owner:
            return f"{provider} ({owner})"
        return provider

    def clean(self):
        if self.user_id or self.group_id:
            super().clean()
        else:
            super(Profile, self).clean()

    def get_base_url(self) -> str:
        if self.provider != self.Provider.GODADDY:
            raise ValueError("Unsupported DNS provider")
        if self.use_sandbox:
            return "https://api.ote-godaddy.com"
        return "https://api.godaddy.com"

    def get_auth_header(self) -> str:
        key = (self.resolve_sigils("api_key") or "").strip()
        secret = (self.resolve_sigils("api_secret") or "").strip()
        if not key or not secret:
            raise ValueError("API credentials are required for DNS deployment")
        return f"sso-key {key}:{secret}"

    def get_customer_id(self) -> str:
        return (self.resolve_sigils("customer_id") or "").strip()

    def get_default_domain(self) -> str:
        return (self.resolve_sigils("default_domain") or "").strip()

    def publish_dns_records(self, records: Iterable["DNSRecord"]):
        from . import dns as dns_utils

        return dns_utils.deploy_records(self, records)


class DNSRecord(Entity):
    """Stored DNS configuration ready for deployment."""

    class Type(models.TextChoices):
        A = "A", "A"
        AAAA = "AAAA", "AAAA"
        CNAME = "CNAME", "CNAME"
        MX = "MX", "MX"
        NS = "NS", "NS"
        SRV = "SRV", "SRV"
        TXT = "TXT", "TXT"

    class Provider(models.TextChoices):
        GODADDY = "godaddy", "GoDaddy"

    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.GODADDY,
    )
    node_manager = models.ForeignKey(
        "NodeManager",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dns_records",
    )
    domain = SigilShortAutoField(
        max_length=253,
        help_text="Base domain such as example.com.",
    )
    name = SigilShortAutoField(
        max_length=253,
        help_text="Record host. Use @ for the zone apex.",
    )
    record_type = models.CharField(
        max_length=10,
        choices=Type.choices,
        default=Type.A,
        verbose_name="Type",
    )
    data = SigilLongAutoField(
        help_text="Record value such as an IP address or hostname.",
    )
    ttl = models.PositiveIntegerField(
        default=600,
        help_text="Time to live in seconds.",
    )
    priority = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Priority for MX and SRV records.",
    )
    port = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Port for SRV records.",
    )
    weight = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Weight for SRV records.",
    )
    service = SigilShortAutoField(
        max_length=50,
        blank=True,
        help_text="Service label for SRV records (for example _sip).",
    )
    protocol = SigilShortAutoField(
        max_length=10,
        blank=True,
        help_text="Protocol label for SRV records (for example _tcp).",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "DNS Record"
        verbose_name_plural = "DNS Records"

    def __str__(self) -> str:
        return f"{self.record_type} {self.fqdn()}"

    def get_domain(self, manager: "NodeManager" | None = None) -> str:
        domain = (self.resolve_sigils("domain") or "").strip()
        if domain:
            return domain.rstrip(".")
        if manager:
            fallback = manager.get_default_domain()
            if fallback:
                return fallback.rstrip(".")
        return ""

    def get_name(self) -> str:
        name = (self.resolve_sigils("name") or "").strip()
        return name or "@"

    def fqdn(self, manager: "NodeManager" | None = None) -> str:
        domain = self.get_domain(manager)
        name = self.get_name()
        if name in {"@", ""}:
            return domain
        if name.endswith("."):
            return name.rstrip(".")
        if domain:
            return f"{name}.{domain}".rstrip(".")
        return name.rstrip(".")

    def to_godaddy_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "data": (self.resolve_sigils("data") or "").strip(),
            "ttl": self.ttl,
        }
        if self.priority is not None:
            payload["priority"] = self.priority
        if self.port is not None:
            payload["port"] = self.port
        if self.weight is not None:
            payload["weight"] = self.weight
        service = (self.resolve_sigils("service") or "").strip()
        if service:
            payload["service"] = service
        protocol = (self.resolve_sigils("protocol") or "").strip()
        if protocol:
            payload["protocol"] = protocol
        return payload

    def mark_deployed(self, manager: "NodeManager" | None = None, timestamp=None) -> None:
        if timestamp is None:
            timestamp = timezone.now()
        update_fields = ["last_synced_at", "last_error"]
        self.last_synced_at = timestamp
        self.last_error = ""
        if manager and self.node_manager_id != getattr(manager, "pk", None):
            self.node_manager = manager
            update_fields.append("node_manager")
        self.save(update_fields=update_fields)

    def mark_error(self, message: str, manager: "NodeManager" | None = None) -> None:
        update_fields = ["last_error"]
        self.last_error = message
        if manager and self.node_manager_id != getattr(manager, "pk", None):
            self.node_manager = manager
            update_fields.append("node_manager")
        self.save(update_fields=update_fields)


class EmailOutbox(Profile):
    """SMTP credentials for sending mail."""

    profile_fields = (
        "host",
        "port",
        "username",
        "password",
        "use_tls",
        "use_ssl",
        "from_email",
    )

    node = models.OneToOneField(
        Node,
        on_delete=models.CASCADE,
        related_name="email_outbox",
        null=True,
        blank=True,
    )
    host = SigilShortAutoField(
        max_length=100,
        help_text=("Gmail: smtp.gmail.com. " "GoDaddy: smtpout.secureserver.net"),
    )
    port = models.PositiveIntegerField(
        default=587,
        help_text=("Gmail: 587 (TLS). " "GoDaddy: 587 (TLS) or 465 (SSL)"),
    )
    username = SigilShortAutoField(
        max_length=100,
        blank=True,
        help_text="Full email address for Gmail or GoDaddy",
    )
    password = SigilShortAutoField(
        max_length=100,
        blank=True,
        help_text="Email account password or app password",
    )
    use_tls = models.BooleanField(
        default=True,
        help_text="Check for Gmail or GoDaddy on port 587",
    )
    use_ssl = models.BooleanField(
        default=False,
        help_text="Check for GoDaddy on port 465; Gmail does not use SSL",
    )
    from_email = SigilShortAutoField(
        blank=True,
        verbose_name="From Email",
        max_length=254,
        help_text="Default From address; usually the same as username",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Disable to remove this outbox from automatic selection.",
    )

    class Meta:
        verbose_name = "Email Outbox"
        verbose_name_plural = "Email Outboxes"

    def __str__(self) -> str:
        address = (self.from_email or "").strip()
        if address:
            return address

        username = (self.username or "").strip()
        host = (self.host or "").strip()
        if username:
            local, sep, domain = username.partition("@")
            if sep and domain:
                return username
            if host:
                sanitized = username.rstrip("@")
                if sanitized:
                    return f"{sanitized}@{host}"
                return host
            return username
        if host:
            return host

        owner = self.owner_display()
        if owner:
            return owner

        return super().__str__()

    def clean(self):
        if self.user_id or self.group_id:
            super().clean()
        else:
            super(Profile, self).clean()

    def get_connection(self):
        return get_connection(
            "django.core.mail.backends.smtp.EmailBackend",
            host=self.host,
            port=self.port,
            username=self.username or None,
            password=self.password or None,
            use_tls=self.use_tls,
            use_ssl=self.use_ssl,
        )

    def send_mail(self, subject, message, recipient_list, from_email=None, **kwargs):
        from_email = from_email or self.from_email or settings.DEFAULT_FROM_EMAIL
        logger.info("EmailOutbox %s queueing email to %s", self.pk, recipient_list)
        return mailer.send(
            subject,
            message,
            recipient_list,
            from_email,
            outbox=self,
            **kwargs,
        )

    def owner_display(self):
        owner = super().owner_display()
        if owner:
            return owner
        return str(self.node) if self.node_id else ""


class NetMessage(Entity):
    """Message propagated across nodes."""

    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="UUID",
    )
    node_origin = models.ForeignKey(
        "Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="originated_net_messages",
    )
    subject = models.CharField(max_length=64, blank=True)
    body = models.CharField(max_length=256, blank=True)
    attachments = models.JSONField(blank=True, null=True)
    filter_node = models.ForeignKey(
        "Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="filtered_net_messages",
        verbose_name="Node",
    )
    filter_node_feature = models.ForeignKey(
        "NodeFeature",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Node feature",
    )
    filter_node_role = models.ForeignKey(
        NodeRole,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="filtered_net_messages",
        verbose_name="Node role",
    )
    filter_current_relation = models.CharField(
        max_length=10,
        blank=True,
        choices=Node.Relation.choices,
        verbose_name="Current relation",
    )
    filter_installed_version = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Installed version",
    )
    filter_installed_revision = models.CharField(
        max_length=40,
        blank=True,
        verbose_name="Installed revision",
    )
    reach = models.ForeignKey(
        NodeRole,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    target_limit = models.PositiveSmallIntegerField(
        default=6,
        blank=True,
        null=True,
        help_text="Maximum number of peers to contact when propagating.",
    )
    propagated_to = models.ManyToManyField(
        Node, blank=True, related_name="received_net_messages"
    )
    created = models.DateTimeField(auto_now_add=True)
    complete = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["-created"]
        verbose_name = "Net Message"
        verbose_name_plural = "Net Messages"

    @classmethod
    def broadcast(
        cls,
        subject: str,
        body: str,
        reach: NodeRole | str | None = None,
        seen: list[str] | None = None,
        attachments: list[dict[str, object]] | None = None,
    ):
        role = None
        if reach:
            if isinstance(reach, NodeRole):
                role = reach
            else:
                role = NodeRole.objects.filter(name=reach).first()
        else:
            role = NodeRole.objects.filter(name="Terminal").first()
        origin = Node.get_local()
        normalized_attachments = cls.normalize_attachments(attachments)
        msg = cls.objects.create(
            subject=subject[:64],
            body=body[:256],
            reach=role,
            node_origin=origin,
            attachments=normalized_attachments or None,
        )
        if normalized_attachments:
            msg.apply_attachments(normalized_attachments)
        msg.propagate(seen=seen or [])
        return msg

    @staticmethod
    def normalize_attachments(
        attachments: object,
    ) -> list[dict[str, object]]:
        if not attachments or not isinstance(attachments, list):
            return []
        normalized: list[dict[str, object]] = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            model_label = item.get("model")
            fields = item.get("fields")
            if not isinstance(model_label, str) or not isinstance(fields, dict):
                continue
            normalized_item: dict[str, object] = {
                "model": model_label,
                "fields": deepcopy(fields),
            }
            if "pk" in item:
                normalized_item["pk"] = item["pk"]
            normalized.append(normalized_item)
        return normalized

    def apply_attachments(
        self, attachments: list[dict[str, object]] | None = None
    ) -> None:
        payload = attachments if attachments is not None else self.attachments or []
        if not payload:
            return
        try:
            objects = list(
                serializers.deserialize(
                    "python", deepcopy(payload), ignorenonexistent=True
                )
            )
        except DeserializationError:
            logger.exception("Failed to deserialize attachments for NetMessage %s", self.pk)
            return
        for obj in objects:
            try:
                obj.save()
            except Exception:
                logger.exception(
                    "Failed to save attachment %s for NetMessage %s",
                    getattr(obj, "object", obj),
                    self.pk,
                )

    def _build_payload(
        self,
        *,
        sender_id: str | None,
        origin_uuid: str | None,
        reach_name: str | None,
        seen: list[str],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "uuid": str(self.uuid),
            "subject": self.subject,
            "body": self.body,
            "seen": list(seen),
            "reach": reach_name,
            "sender": sender_id,
            "origin": origin_uuid,
        }
        if self.attachments:
            payload["attachments"] = self.attachments
        if self.filter_node:
            payload["filter_node"] = str(self.filter_node.uuid)
        if self.filter_node_feature:
            payload["filter_node_feature"] = self.filter_node_feature.slug
        if self.filter_node_role:
            payload["filter_node_role"] = self.filter_node_role.name
        if self.filter_current_relation:
            payload["filter_current_relation"] = self.filter_current_relation
        if self.filter_installed_version:
            payload["filter_installed_version"] = self.filter_installed_version
        if self.filter_installed_revision:
            payload["filter_installed_revision"] = self.filter_installed_revision
        return payload

    @staticmethod
    def _serialize_payload(payload: dict[str, object]) -> str:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _sign_payload(payload_json: str, private_key) -> str | None:
        if not private_key:
            return None
        try:
            signature = private_key.sign(
                payload_json.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception:
            return None
        return base64.b64encode(signature).decode()

    def queue_for_node(self, node: "Node", seen: list[str]) -> None:
        """Queue this message for later delivery to ``node``."""

        if node.current_relation != Node.Relation.DOWNSTREAM:
            return

        now = timezone.now()
        expires_at = now + timedelta(hours=1)
        normalized_seen = [str(value) for value in seen]
        entry, created = PendingNetMessage.objects.get_or_create(
            node=node,
            message=self,
            defaults={
                "seen": normalized_seen,
                "stale_at": expires_at,
            },
        )
        if created:
            entry.queued_at = now
            entry.save(update_fields=["queued_at"])
        else:
            entry.seen = normalized_seen
            entry.stale_at = expires_at
            entry.queued_at = now
            entry.save(update_fields=["seen", "stale_at", "queued_at"])
        self._trim_queue(node)

    def clear_queue_for_node(self, node: "Node") -> None:
        PendingNetMessage.objects.filter(node=node, message=self).delete()

    def _trim_queue(self, node: "Node") -> None:
        limit = max(int(node.message_queue_length or 0), 0)
        if limit == 0:
            PendingNetMessage.objects.filter(node=node).delete()
            return
        qs = PendingNetMessage.objects.filter(node=node).order_by("-queued_at")
        keep_ids = list(qs.values_list("pk", flat=True)[:limit])
        if keep_ids:
            PendingNetMessage.objects.filter(node=node).exclude(pk__in=keep_ids).delete()
        else:
            qs.delete()

    @classmethod
    def receive_payload(
        cls,
        data: dict[str, object],
        *,
        sender: "Node",
    ) -> "NetMessage":
        msg_uuid = data.get("uuid")
        if not msg_uuid:
            raise ValueError("uuid required")
        subject = (data.get("subject") or "")[:64]
        body = (data.get("body") or "")[:256]
        attachments = cls.normalize_attachments(data.get("attachments"))
        reach_name = data.get("reach")
        reach_role = None
        if reach_name:
            reach_role = NodeRole.objects.filter(name=reach_name).first()
        filter_node_uuid = data.get("filter_node")
        filter_node = None
        if filter_node_uuid:
            filter_node = Node.objects.filter(uuid=filter_node_uuid).first()
        filter_feature_slug = data.get("filter_node_feature")
        filter_feature = None
        if filter_feature_slug:
            filter_feature = NodeFeature.objects.filter(slug=filter_feature_slug).first()
        filter_role_name = data.get("filter_node_role")
        filter_role = None
        if filter_role_name:
            filter_role = NodeRole.objects.filter(name=filter_role_name).first()
        filter_relation_value = data.get("filter_current_relation")
        filter_relation = ""
        if filter_relation_value:
            relation = Node.normalize_relation(filter_relation_value)
            filter_relation = relation.value if relation else ""
        filter_installed_version = (data.get("filter_installed_version") or "")[:20]
        filter_installed_revision = (data.get("filter_installed_revision") or "")[:40]
        seen_values = data.get("seen", [])
        if not isinstance(seen_values, list):
            seen_values = list(seen_values)  # type: ignore[arg-type]
        normalized_seen = [str(value) for value in seen_values if value is not None]
        origin_id = data.get("origin")
        origin_node = None
        if origin_id:
            origin_node = Node.objects.filter(uuid=origin_id).first()
        if not origin_node:
            origin_node = sender
        msg, created = cls.objects.get_or_create(
            uuid=msg_uuid,
            defaults={
                "subject": subject,
                "body": body,
                "reach": reach_role,
                "node_origin": origin_node,
                "attachments": attachments or None,
                "filter_node": filter_node,
                "filter_node_feature": filter_feature,
                "filter_node_role": filter_role,
                "filter_current_relation": filter_relation,
                "filter_installed_version": filter_installed_version,
                "filter_installed_revision": filter_installed_revision,
            },
        )
        if not created:
            msg.subject = subject
            msg.body = body
            update_fields = ["subject", "body"]
            if reach_role and msg.reach_id != reach_role.id:
                msg.reach = reach_role
                update_fields.append("reach")
            if msg.node_origin_id is None and origin_node:
                msg.node_origin = origin_node
                update_fields.append("node_origin")
            if attachments and msg.attachments != attachments:
                msg.attachments = attachments
                update_fields.append("attachments")
            field_updates = {
                "filter_node": filter_node,
                "filter_node_feature": filter_feature,
                "filter_node_role": filter_role,
                "filter_current_relation": filter_relation,
                "filter_installed_version": filter_installed_version,
                "filter_installed_revision": filter_installed_revision,
            }
            for field, value in field_updates.items():
                if getattr(msg, field) != value:
                    setattr(msg, field, value)
                    update_fields.append(field)
            if update_fields:
                msg.save(update_fields=update_fields)
        if attachments:
            msg.apply_attachments(attachments)
        msg.propagate(seen=normalized_seen)
        return msg

    def propagate(self, seen: list[str] | None = None):
        from core.notifications import notify
        import random
        import requests

        displayed = notify(self.subject, self.body)
        local = Node.get_local()
        if displayed:
            cutoff = timezone.now() - timedelta(days=7)
            prune_qs = type(self).objects.filter(created__lt=cutoff)
            if local:
                prune_qs = prune_qs.filter(
                    models.Q(node_origin=local) | models.Q(node_origin__isnull=True)
                )
            else:
                prune_qs = prune_qs.filter(node_origin__isnull=True)
            if self.pk:
                prune_qs = prune_qs.exclude(pk=self.pk)
            prune_qs.delete()
        if local and not self.node_origin_id:
            self.node_origin = local
            self.save(update_fields=["node_origin"])
        origin_uuid = None
        if self.node_origin_id:
            origin_uuid = str(self.node_origin.uuid)
        elif local:
            origin_uuid = str(local.uuid)
        private_key = None
        seen = list(seen or [])
        local_id = None
        if local:
            local_id = str(local.uuid)
            if local_id not in seen:
                seen.append(local_id)
            private_key = local.get_private_key()
        for node_id in seen:
            node = Node.objects.filter(uuid=node_id).first()
            if node and (not local or node.pk != local.pk):
                self.propagated_to.add(node)

        filtered_nodes = Node.objects.all()
        if self.filter_node_id:
            filtered_nodes = filtered_nodes.filter(pk=self.filter_node_id)
        if self.filter_node_feature_id:
            filtered_nodes = filtered_nodes.filter(
                features__pk=self.filter_node_feature_id
            )
        if self.filter_node_role_id:
            filtered_nodes = filtered_nodes.filter(role_id=self.filter_node_role_id)
        if self.filter_current_relation:
            filtered_nodes = filtered_nodes.filter(
                current_relation=self.filter_current_relation
            )
        if self.filter_installed_version:
            filtered_nodes = filtered_nodes.filter(
                installed_version=self.filter_installed_version
            )
        if self.filter_installed_revision:
            filtered_nodes = filtered_nodes.filter(
                installed_revision=self.filter_installed_revision
            )

        filtered_nodes = filtered_nodes.distinct()

        if local:
            filtered_nodes = filtered_nodes.exclude(pk=local.pk)
        total_known = filtered_nodes.count()

        remaining = list(
            filtered_nodes.exclude(
                pk__in=self.propagated_to.values_list("pk", flat=True)
            )
        )
        if not remaining:
            self.complete = True
            self.save(update_fields=["complete"])
            return

        limit = self.target_limit or 6
        target_limit = min(limit, len(remaining))

        reach_source = self.filter_node_role or self.reach
        reach_name = reach_source.name if reach_source else None
        role_map = {
            "Interface": ["Interface", "Terminal"],
            "Terminal": ["Terminal"],
            "Control": ["Control", "Terminal"],
            "Satellite": ["Satellite", "Control", "Terminal"],
            "Watchtower": [
                "Watchtower",
                "Satellite",
                "Control",
                "Terminal",
            ],
            "Constellation": [
                "Watchtower",
                "Satellite",
                "Control",
                "Terminal",
            ],
        }
        selected: list[Node] = []
        if self.filter_node_id:
            target = next((n for n in remaining if n.pk == self.filter_node_id), None)
            if target:
                selected = [target]
            else:
                self.complete = True
                self.save(update_fields=["complete"])
                return
        else:
            if self.filter_node_role_id:
                role_order = [reach_name]
            else:
                role_order = role_map.get(reach_name, [None])
            for role_name in role_order:
                if role_name is None:
                    role_nodes = remaining[:]
                else:
                    role_nodes = [
                        n for n in remaining if n.role and n.role.name == role_name
                    ]
                random.shuffle(role_nodes)
                for n in role_nodes:
                    selected.append(n)
                    remaining.remove(n)
                    if len(selected) >= target_limit:
                        break
                if len(selected) >= target_limit:
                    break

        if not selected:
            self.complete = True
            self.save(update_fields=["complete"])
            return

        seen_list = seen.copy()
        selected_ids = [str(n.uuid) for n in selected]
        payload_seen = seen_list + selected_ids
        for node in selected:
            payload = self._build_payload(
                sender_id=local_id,
                origin_uuid=origin_uuid,
                reach_name=reach_name,
                seen=payload_seen,
            )
            payload_json = self._serialize_payload(payload)
            headers = {"Content-Type": "application/json"}
            signature = self._sign_payload(payload_json, private_key)
            if signature:
                headers["X-Signature"] = signature
            success = False
            try:
                response = requests.post(
                    f"http://{node.address}:{node.port}/nodes/net-message/",
                    data=payload_json,
                    headers=headers,
                    timeout=1,
                )
                success = bool(response.ok)
            except Exception:
                logger.exception(
                    "Failed to propagate NetMessage %s to node %s",
                    self.pk,
                    node.pk,
                )
            if success:
                self.clear_queue_for_node(node)
            else:
                self.queue_for_node(node, payload_seen)
            self.propagated_to.add(node)

        save_fields: list[str] = []
        if total_known and self.propagated_to.count() >= total_known:
            self.complete = True
            save_fields.append("complete")

        if save_fields:
            self.save(update_fields=save_fields)


class PendingNetMessage(models.Model):
    """Queued :class:`NetMessage` awaiting delivery to a downstream node."""

    node = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name="pending_net_messages"
    )
    message = models.ForeignKey(
        NetMessage,
        on_delete=models.CASCADE,
        related_name="pending_deliveries",
    )
    seen = models.JSONField(default=list)
    queued_at = models.DateTimeField(auto_now_add=True)
    stale_at = models.DateTimeField()

    class Meta:
        unique_together = ("node", "message")
        ordering = ("queued_at",)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.message_id} → {self.node_id}"

    @property
    def is_stale(self) -> bool:
        return self.stale_at <= timezone.now()

class ContentSample(Entity):
    """Collected content such as text snippets or screenshots."""

    TEXT = "TEXT"
    IMAGE = "IMAGE"
    KIND_CHOICES = [(TEXT, "Text"), (IMAGE, "Image")]

    name = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    content = models.TextField(blank=True)
    path = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=10, default="", blank=True)
    hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    transaction_uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=True,
        db_index=True,
        verbose_name="transaction UUID",
    )
    node = models.ForeignKey(Node, on_delete=models.SET_NULL, null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Content Sample"
        verbose_name_plural = "Content Samples"

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.get(pk=self.pk)
            if original.transaction_uuid != self.transaction_uuid:
                raise ValidationError(
                    {"transaction_uuid": "Cannot modify transaction UUID"}
                )
        if self.node_id is None:
            self.node = Node.get_local()
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return str(self.name)


class ContentClassifier(Entity):
    """Configured callable that classifies :class:`ContentSample` objects."""

    slug = models.SlugField(max_length=100, unique=True)
    label = models.CharField(max_length=150)
    kind = models.CharField(max_length=10, choices=ContentSample.KIND_CHOICES)
    entrypoint = models.CharField(max_length=255, help_text="Dotted path to classifier callable")
    run_by_default = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["label"]
        verbose_name = "Content Classifier"
        verbose_name_plural = "Content Classifiers"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.label


class ContentTag(Entity):
    """Tag that can be attached to classified content samples."""

    slug = models.SlugField(max_length=100, unique=True)
    label = models.CharField(max_length=150)

    class Meta:
        ordering = ["label"]
        verbose_name = "Content Tag"
        verbose_name_plural = "Content Tags"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.label


class ContentClassification(Entity):
    """Link between a sample, classifier, and assigned tag."""

    sample = models.ForeignKey(
        ContentSample, on_delete=models.CASCADE, related_name="classifications"
    )
    classifier = models.ForeignKey(
        ContentClassifier, on_delete=models.CASCADE, related_name="classifications"
    )
    tag = models.ForeignKey(
        ContentTag, on_delete=models.CASCADE, related_name="classifications"
    )
    confidence = models.FloatField(null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("sample", "classifier", "tag")
        ordering = ["-created_at"]
        verbose_name = "Content Classification"
        verbose_name_plural = "Content Classifications"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.sample} → {self.tag}"


UserModel = get_user_model()


class User(UserModel):
    class Meta:
        proxy = True
        app_label = "nodes"
        verbose_name = UserModel._meta.verbose_name
        verbose_name_plural = UserModel._meta.verbose_name_plural
