from __future__ import annotations

from collections.abc import Iterable
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone as datetime_timezone
import ipaddress
import json
import logging
import os
import re
import socket
import uuid
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from urllib.parse import urlparse, urlunsplit

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core import serializers
from django.core.serializers.base import DeserializationError
from django.core.validators import validate_ipv46_address, validate_ipv6_address
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.db.utils import DatabaseError
from django.dispatch import Signal, receiver
from django.utils import timezone

from apps.core.notifications import LcdChannel
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.core.notifications import notify_async
from apps.emails import mailer
from apps.sigils.fields import SigilShortAutoField
from apps.users.models import Profile
from utils import revision

from .features import NodeFeature, NodeFeatureMixin
from .networking import NodeNetworkingMixin
from .role import NodeRole
from .utils import ROLE_RENAMES, _format_upgrade_body, _upgrade_in_progress

if TYPE_CHECKING:  # pragma: no cover - used for type checking
    from apps.dns.models import GoDaddyDNSRecord

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from apps.nodes.logging import get_register_local_node_logger

logger = logging.getLogger(__name__)
local_registration_logger = get_register_local_node_logger()


def _redact_mac_for_log(mac: str | None) -> str:
    """Return a deterministic, non-plaintext MAC token for logging."""

    if not mac:
        return ""

    normalized = "".join(char.lower() for char in str(mac) if char.isalnum())
    if not normalized:
        return "***REDACTED***"

    digest = hashes.Hash(hashes.SHA256())
    digest.update(normalized.encode("utf-8"))
    return f"***REDACTED***-{digest.finalize().hex()[:12]}"


class Node(NodeFeatureMixin, NodeNetworkingMixin, Entity):
    """Information about a running node in the network."""

    DEFAULT_BADGE_COLOR = "#28a745"
    _LOCAL_CACHE_TIMEOUT = timedelta(seconds=60)
    _local_cache: dict[str, tuple[Optional["Node"], datetime]] = {}
    ROLE_BADGE_COLORS = {
        "Watchtower": "#daa520",  # goldenrod
        "Constellation": "#daa520",  # legacy alias
        "Control": "#673ab7",  # deep purple
    }

    class Relation(models.TextChoices):
        UPSTREAM = "UPSTREAM", "Upstream"
        DOWNSTREAM = "DOWNSTREAM", "Downstream"
        PEER = "PEER", "Peer"
        SIBLING = "SIBLING", "Sibling"
        SELF = "SELF", "Self"

    class MeshEnrollmentState(models.TextChoices):
        UNENROLLED = "UNENROLLED", "Unenrolled"
        PENDING = "PENDING", "Pending"
        ENROLLED = "ENROLLED", "Enrolled"
        FAILED = "FAILED", "Failed"

    hostname = models.CharField(max_length=100)
    base_site = models.ForeignKey(
        Site,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nodes",
        verbose_name=("Base site"),
        help_text=("Site that provides the preferred domain for this node."),
    )
    network_hostname = models.CharField(max_length=253, blank=True)
    ipv4_address = models.TextField(blank=True)
    ipv6_address = models.CharField(
        max_length=39,
        blank=True,
        validators=[validate_ipv6_address],
    )
    address = models.CharField(
        max_length=45,
        blank=True,
        validators=[validate_ipv46_address],
    )
    mac_address = models.CharField(max_length=17, blank=True)
    port = models.PositiveIntegerField(default=8888)
    trusted = models.BooleanField(
        default=False,
        help_text="Mark the node as trusted for network interactions.",
    )
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
    last_updated = models.DateTimeField(auto_now=True, verbose_name=_("Last updated"))
    public_endpoint = models.SlugField(blank=True, unique=True)
    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="UUID",
    )
    public_key = models.TextField(blank=True)
    base_path = models.CharField(max_length=255, blank=True)
    ipc_scheme = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Optional sibling IPC transport scheme (for example unix_socket).",
    )
    ipc_path = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional sibling IPC socket path override.",
    )
    installed_version = models.CharField(max_length=20, blank=True)
    installed_revision = models.CharField(max_length=40, blank=True)
    mesh_enrollment_state = models.CharField(
        max_length=20,
        choices=MeshEnrollmentState.choices,
        default=MeshEnrollmentState.UNENROLLED,
    )
    mesh_key_fingerprint_metadata = models.JSONField(default=dict, blank=True)
    last_mesh_heartbeat = models.DateTimeField(null=True, blank=True)
    mesh_capability_flags = models.JSONField(default=list, blank=True)
    upgrade_canaries = models.ManyToManyField(
        "self",
        blank=True,
        symmetrical=False,
        related_name="upgrade_targets",
        help_text=(
            "Nodes that must be running and upgraded before this node can "
            "auto-upgrade."
        ),
    )
    upgrade_policies = models.ManyToManyField(
        "nodes.UpgradePolicy",
        through="nodes.NodeUpgradePolicyAssignment",
        related_name="nodes",
        blank=True,
        help_text="Upgrade policies applied to this node.",
    )
    features = models.ManyToManyField(
        "nodes.NodeFeature",
        through="nodes.NodeFeatureAssignment",
        related_name="nodes",
        blank=True,
    )
    preferred_port: int = int(os.environ.get("PORT", 8888))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["mac_address"],
                condition=~Q(mac_address=""),
                name="nodes_node_mac_address_unique",
            ),
        ]
        verbose_name = "Node"
        verbose_name_plural = "Nodes"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return the hostname and port for display."""
        return f"{self.hostname}:{self.port}"

    def get_base_domain(self) -> str:
        """Return the preferred domain provided by the linked site if available."""

        if not self.base_site_id:
            return ""
        try:
            domain = (getattr(self.base_site, "domain", "") or "").strip()
        except Site.DoesNotExist:
            return ""
        return domain

    def get_preferred_hostname(self) -> str:
        """Return the hostname prioritized for external communication."""

        base_domain = self.get_base_domain()
        if base_domain:
            return base_domain
        return self.hostname

    @classmethod
    def default_base_path(cls) -> Path:
        """Return the default filesystem base path for node assets."""

        return Path(settings.BASE_DIR) / "work" / "nodes"

    def get_base_path(self) -> Path:
        """Return the configured base path or the default nodes directory."""

        base_path = (self.base_path or "").strip()
        return Path(base_path) if base_path else self.default_base_path()

    def get_ipc_socket_path(self) -> Path | None:
        """Return the configured sibling IPC socket path when available."""

        if (self.ipc_scheme or "unix_socket").strip() not in {"", "unix_socket"}:
            return None
        configured = (self.ipc_path or "").strip()
        if configured:
            return Path(configured)
        endpoint = (self.public_endpoint or "").strip()
        if not endpoint:
            return None
        return self.get_base_path() / "ipc" / f"{endpoint}.sock"

    def get_sibling_ipc_status(self) -> dict[str, object]:
        """Return current sibling IPC status details for diagnostics/admin."""

        socket_path = self.get_ipc_socket_path()
        enabled = bool(getattr(settings, "NODES_ENABLE_SIBLING_IPC", False))
        if not enabled:
            return {"enabled": False, "status": "disabled", "path": str(socket_path or "")}
        if not socket_path:
            return {"enabled": True, "status": "unconfigured", "path": ""}
        if not socket_path.exists():
            return {"enabled": True, "status": "missing", "path": str(socket_path)}
        try:
            socket_mode = socket_path.stat().st_mode & 0o777
        except OSError:
            return {"enabled": True, "status": "error", "path": str(socket_path)}
        return {
            "enabled": True,
            "status": "ready",
            "path": str(socket_path),
            "mode": f"{socket_mode:o}",
        }

    @classmethod
    def get_preferred_port(cls) -> int:
        """Return the port configured when the instance started."""

        try:
            port = int(cls.preferred_port)
        except (TypeError, ValueError):
            return 8888
        if port <= 0 or port > 65535:
            return 8888
        return port

    @classmethod
    def _detect_managed_site(cls) -> tuple[Site | None, str, bool]:
        """Return the primary managed site, domain, and HTTPS preference."""

        try:
            SiteModel = apps.get_model("sites", "Site")
        except Exception:
            return None, "", False

        try:
            site = (
                SiteModel.objects.filter(managed=True)
                .only("domain", "require_https")
                .order_by("id")
                .first()
                or SiteModel.objects.only("domain", "require_https").order_by("id").first()
            )
        except DatabaseError:
            return None, "", False

        if not site:
            return None, "", False

        domain = (getattr(site, "domain", "") or "").strip()
        if not domain or domain.lower() == "localhost":
            return None, "", False

        try:
            ipaddress.ip_address(domain)
        except ValueError:
            return site, domain, bool(getattr(site, "require_https", False))
        return None, "", False

    @classmethod
    def _preferred_site_port(cls, require_https: bool) -> int:
        """Return the preferred site port for HTTPS or HTTP."""
        return 443 if require_https else 80

    @staticmethod
    def get_current_mac() -> str:
        """Return the MAC address of the current host."""
        return ":".join(re.findall("..", f"{uuid.getnode():012x}"))

    @classmethod
    def get_local(cls):
        """Return the node representing the current host if it exists.

        When the runtime MAC address changes (for example after NIC
        replacement, virtualization changes, or image cloning), the local
        ``SELF`` node may still exist with a stale MAC. In that case this
        method attempts to refresh the stored MAC so local-only tasks keep
        running without manual re-registration.
        """
        mac = cls.get_current_mac()
        now = timezone.now()

        cached = cls._local_cache.get(mac)
        if cached:
            node, expires_at = cached
            if expires_at > now:
                if node is None:
                    return None
                try:
                    if cls.objects.filter(pk=node.pk).exists():
                        return node
                except DatabaseError:
                    logger.debug(
                        "nodes.Node.get_local skipped: database unavailable",
                        exc_info=True,
                    )
                    return None
                cls._local_cache.pop(mac, None)

        try:
            node = cls.objects.filter(mac_address__iexact=mac).first()
            if node:
                cls._local_cache[mac] = (node, now + cls._LOCAL_CACHE_TIMEOUT)
                return node
            node = cls.objects.filter(current_relation=cls.Relation.SELF).first()
            if not node:
                return None

            stored_mac = (node.mac_address or "").strip().lower()
            current_mac = mac.strip().lower()
            should_cache = True
            if stored_mac != current_mac:
                node.mac_address = mac
                try:
                    node.save(update_fields=["mac_address"])
                except IntegrityError:
                    node.mac_address = stored_mac
                    logger.warning(
                        "nodes.Node.get_local detected MAC mismatch for self node and could not update due to MAC uniqueness conflict",
                        extra={
                            "runtime_mac_redacted": _redact_mac_for_log(mac),
                            "stored_mac_redacted": _redact_mac_for_log(stored_mac),
                            "node_id": node.pk,
                        },
                        exc_info=True,
                    )
                    if transaction.get_connection().in_atomic_block:
                        transaction.set_rollback(False)
                    try:
                        node = cls.objects.filter(mac_address__iexact=mac).first() or node
                        should_cache = (node.mac_address or "").strip().lower() == current_mac
                    except DatabaseError:
                        should_cache = False
                except DatabaseError:
                    node.mac_address = stored_mac
                    should_cache = False
                    logger.warning(
                        "nodes.Node.get_local could not save MAC update for self node due to a database error",
                        extra={
                            "runtime_mac_redacted": _redact_mac_for_log(mac),
                            "stored_mac_redacted": _redact_mac_for_log(stored_mac),
                            "node_id": node.pk,
                        },
                        exc_info=True,
                    )
                else:
                    logger.warning(
                        "nodes.Node.get_local refreshed stale self-node MAC address",
                        extra={
                            "runtime_mac_redacted": _redact_mac_for_log(mac),
                            "stored_mac_redacted": _redact_mac_for_log(stored_mac),
                            "node_id": node.pk,
                        },
                    )

            if should_cache:
                cls._local_cache[mac] = (node, now + cls._LOCAL_CACHE_TIMEOUT)
            return node
        except DatabaseError:
            logger.debug("nodes.Node.get_local skipped: database unavailable", exc_info=True)
            return None

    @classmethod
    def default_instance(cls):
        """Return the preferred node for sigil resolution."""

        local = cls.get_local()
        if local:
            return local
        return cls.objects.order_by("?").first()

    @classmethod
    def register_current(cls, notify_peers: bool = True):
        """Create or update the :class:`Node` entry for this host."""
        from apps.nodes.services.registration import register_current

        return register_current(cls, notify_peers=notify_peers)

    def notify_peers_of_update(self):
        """Attempt to update this node's registration with known peers."""
        from apps.nodes.services.notifications import notify_peers_of_update

        notify_peers_of_update(self)

    def ensure_keys(self):
        """Ensure the node has a valid RSA key pair on disk."""
        from apps.nodes.services.crypto import ensure_keys

        ensure_keys(self)

    def get_private_key(self):
        """Return the private key for this node if available."""
        from apps.nodes.services.crypto import get_private_key

        return get_private_key(self)

    @staticmethod
    def sign_payload(
        payload: str, private_key
    ) -> tuple[str | None, str | None]:
        """Sign ``payload`` with ``private_key`` and return a base64 signature."""
        from apps.nodes.services.crypto import sign_payload

        return sign_payload(payload, private_key)

    @property
    def is_local(self):
        """Determine if this node represents the current host."""
        current_mac = self.get_current_mac()
        stored_mac = (self.mac_address or "").strip()
        if stored_mac:
            normalized_stored = stored_mac.replace("-", ":").lower()
            normalized_current = current_mac.replace("-", ":").lower()
            return normalized_stored == normalized_current
        return self.current_relation == self.Relation.SELF

    @classmethod
    def _generate_unique_public_endpoint(
        cls, value: str | None, *, exclude_pk: int | None = None
    ) -> str:
        """Return a unique public endpoint slug for ``value``."""

        field = cls._meta.get_field("public_endpoint")
        max_length = field.max_length
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
        """Persist the node and ensure derived fields remain consistent."""
        update_fields = kwargs.get("update_fields")

        def include_update_field(field: str):
            """Ensure the field is listed in update_fields when saving."""
            nonlocal update_fields
            if update_fields is None:
                return
            fields = set(update_fields)
            if field in fields:
                return
            fields.add(field)
            update_fields = tuple(fields)
            kwargs["update_fields"] = update_fields

        if self.mac_address is None:
            self.mac_address = ""

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
        endpoint_field = self._meta.get_field("public_endpoint")
        endpoint_max_length = endpoint_field.max_length
        endpoint_value = slugify(self.public_endpoint or "")
        if len(endpoint_value) > endpoint_max_length:
            endpoint_value = endpoint_value[:endpoint_max_length]
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
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if self.pk:
            if is_new:
                self._apply_role_manual_features()
                self._apply_role_upgrade_policy()
            self._apply_role_auto_features()
            self.refresh_features()

    def _apply_role_upgrade_policy(self) -> None:
        """Assign the default upgrade policy for this node's role."""
        if not self.role_id:
            return
        try:
            if self.upgrade_policies.exists():
                return
        except DatabaseError:
            return

        policy_id = getattr(self.role, "default_upgrade_policy_id", None)
        if not policy_id:
            return

        assignment_model = apps.get_model("nodes", "NodeUpgradePolicyAssignment")
        if assignment_model is None:
            return
        assignment_model.objects.get_or_create(node_id=self.pk, policy_id=policy_id)

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
            node=self,
            **kwargs,
        )

node_information_updated = Signal()


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
    """Notify listeners when a peer node reports startup changes."""
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

UserModel = get_user_model()


# Backwards-compatibility access for legacy imports from this module path.
def __getattr__(name: str):
    if name in {"NetMessage", "PendingNetMessage"}:
        from . import net_message

        return getattr(net_message, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class User(UserModel):
    class Meta:
        proxy = True
        app_label = "nodes"
        verbose_name = UserModel._meta.verbose_name
        verbose_name_plural = UserModel._meta.verbose_name_plural
