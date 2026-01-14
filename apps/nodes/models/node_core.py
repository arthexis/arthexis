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
from django.db import models
from django.db.models import Q
from django.db.utils import DatabaseError, IntegrityError
from django.dispatch import Signal, receiver
from django.utils import timezone

from apps.core.notifications import LcdChannel
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.core.notifications import notify_async
from apps.emails import mailer
from apps.release.models import PackageRelease
from apps.sigils.fields import SigilShortAutoField
from apps.users.models import Profile
from utils import revision

from .features import NodeFeature, NodeFeatureMixin
from .networking import NodeNetworkingMixin

if TYPE_CHECKING:  # pragma: no cover - used for type checking
    from apps.dns.models import GoDaddyDNSRecord

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from apps.nodes.logging import get_register_local_node_logger

logger = logging.getLogger(__name__)
local_registration_logger = get_register_local_node_logger()


class NameRepresentationMixin:
    """Provide a name-based ``__str__`` for models with a ``name`` field."""

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


ROLE_RENAMES: dict[str, str] = {"Constellation": "Watchtower"}
ROLE_ACRONYMS: dict[str, str] = {
    "Terminal": "TERM",
    "Control": "CTRL",
    "Satellite": "SATL",
    "Watchtower": "WTTW",
    "Constellation": "CONS",
}


class Platform(NameRepresentationMixin, Entity):
    """Supported hardware and operating system combinations."""

    name = models.CharField(max_length=100, unique=True)
    hardware = models.CharField(max_length=100)
    architecture = models.CharField(max_length=50, blank=True)
    os_name = models.CharField(max_length=100)
    os_version = models.CharField(max_length=50)

    class Meta:
        ordering = ["name"]
        verbose_name = "Platform"
        verbose_name_plural = "Platforms"
        constraints = [
            models.UniqueConstraint(
                fields=["hardware", "architecture", "os_name", "os_version"],
                name="nodes_platform_hardware_os_unique",
            )
        ]

def _upgrade_in_progress() -> bool:
    lock_file = Path(settings.BASE_DIR) / ".locks" / "upgrade_in_progress.lck"
    return lock_file.exists()


class NodeRoleManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)

    def create(self, **kwargs):
        name = kwargs.get("name")
        if name:
            existing = self.filter(name=name).first()
            if existing:
                update_fields = []
                for field, value in kwargs.items():
                    if field == "name":
                        continue
                    if value is not None and getattr(existing, field, None) != value:
                        setattr(existing, field, value)
                        update_fields.append(field)

                if update_fields:
                    existing.save(update_fields=update_fields)

                return existing

        try:
            return super().create(**kwargs)
        except IntegrityError:
            if name:
                existing = self.filter(name=name).first()
                if existing:
                    return existing
            raise


class NodeRole(NameRepresentationMixin, Entity):
    """Assignable role for a :class:`Node`."""

    name = models.CharField(max_length=50, unique=True)
    acronym = models.CharField(max_length=4, unique=True, null=True, blank=True)
    description = models.CharField(max_length=200, blank=True)

    objects = NodeRoleManager()

    class Meta:
        ordering = ["name"]
        verbose_name = "Node Role"
        verbose_name_plural = "Node Roles"

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

def get_terminal_role():
    """Return the NodeRole representing a Terminal if it exists."""
    return NodeRole.objects.filter(name="Terminal").first()


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
        SELF = "SELF", "Self"

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
    installed_version = models.CharField(max_length=20, blank=True)
    installed_revision = models.CharField(max_length=40, blank=True)
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
        return 443 if require_https else 80

    @staticmethod
    def get_current_mac() -> str:
        """Return the MAC address of the current host."""
        return ":".join(re.findall("..", f"{uuid.getnode():012x}"))

    @classmethod
    def get_local(cls):
        """Return the node representing the current host if it exists."""
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
            node = (
                cls.objects.filter(current_relation=cls.Relation.SELF)
                .filter(mac_address__in=["", None])
                .first()
            )
            if node:
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
        """Create or update the :class:`Node` entry for this host.

        Parameters
        ----------
        notify_peers:
            When ``True`` (the default) the node will broadcast an update to
            known peers after registration.  Callers that run in maintenance
            contexts where network communication should be avoided can disable
            the broadcast by passing ``False``.
        """
        hostname_override = (
            os.environ.get("NODE_HOSTNAME")
            or os.environ.get("HOSTNAME")
            or ""
        )
        hostname_override = hostname_override.strip()
        hostname = hostname_override or socket.gethostname()

        network_hostname = os.environ.get("NODE_PUBLIC_HOSTNAME", "").strip()
        if not network_hostname:
            fqdn = socket.getfqdn(hostname)
            if fqdn and "." in fqdn:
                network_hostname = fqdn

        ipv4_override = os.environ.get("NODE_PUBLIC_IPV4", "").strip()
        ipv6_override = os.environ.get("NODE_PUBLIC_IPV6", "").strip()

        ipv4_candidates: list[str] = []
        ipv6_candidates: list[str] = []

        for override, version in ((ipv4_override, 4), (ipv6_override, 6)):
            override = override.strip()
            if not override:
                continue
            try:
                parsed = ipaddress.ip_address(override)
            except ValueError:
                continue
            if parsed.version == version:
                if version == 4 and override not in ipv4_candidates:
                    ipv4_candidates.append(override)
                elif version == 6 and override not in ipv6_candidates:
                    ipv6_candidates.append(override)

        resolve_hosts: list[str] = []
        for value in (network_hostname, hostname_override, hostname):
            value = (value or "").strip()
            if value and value not in resolve_hosts:
                resolve_hosts.append(value)

        resolved_ipv4, resolved_ipv6 = cls._resolve_ip_addresses(*resolve_hosts)
        for ip_value in resolved_ipv4:
            if ip_value not in ipv4_candidates:
                ipv4_candidates.append(ip_value)
        for ip_value in resolved_ipv6:
            if ip_value not in ipv6_candidates:
                ipv6_candidates.append(ip_value)

        try:
            direct_address = socket.gethostbyname(hostname)
        except OSError:
            direct_address = ""

        if direct_address and direct_address not in ipv4_candidates:
            ipv4_candidates.append(direct_address)

        ordered_ipv4 = cls.order_ipv4_addresses(
            cls.sanitize_ipv4_addresses(ipv4_candidates)
        )
        ipv4_address = ordered_ipv4[0] if ordered_ipv4 else ""
        serialized_ipv4 = ",".join(ordered_ipv4) if ordered_ipv4 else ""
        ipv6_address = cls._select_preferred_ip(ipv6_candidates) or ""

        managed_site, site_domain, site_requires_https = cls._detect_managed_site()

        preferred_contact = ipv4_address or ipv6_address or direct_address or "127.0.0.1"
        if site_domain:
            hostname = site_domain
            network_hostname = site_domain
            preferred_contact = site_domain

        port = cls.get_preferred_port()
        if site_domain:
            port = cls._preferred_site_port(site_requires_https)
        base_path = str(cls.default_base_path())
        ver_path = Path(settings.BASE_DIR) / "VERSION"
        installed_version = ver_path.read_text().strip() if ver_path.exists() else ""
        rev_value = revision.get_revision()
        installed_revision = rev_value if rev_value else ""
        mac = cls.get_current_mac()
        local_registration_logger.info(
            "Local node registration started hostname=%s mac=%s", hostname, mac
        )
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
            "network_hostname": network_hostname,
            "ipv4_address": serialized_ipv4,
            "ipv6_address": ipv6_address,
            "address": preferred_contact,
            "port": port,
            "trusted": True,
            "base_path": base_path,
            "installed_version": installed_version,
            "installed_revision": installed_revision,
            "public_endpoint": slug,
            "mac_address": mac,
            "current_relation": cls.Relation.SELF,
        }
        if managed_site:
            defaults["base_site"] = managed_site
        role_lock = Path(settings.BASE_DIR) / ".locks" / "role.lck"
        role_name = role_lock.read_text().strip() if role_lock.exists() else "Terminal"
        role_name = ROLE_RENAMES.get(role_name, role_name)
        desired_role = NodeRole.objects.filter(name=role_name).first()

        if node:
            update_fields = []
            for field, value in defaults.items():
                current = getattr(node, field)
                if isinstance(value, str):
                    value = value or ""
                    current = current or ""
                if current != value:
                    setattr(node, field, value)
                    update_fields.append(field)
            if desired_role and node.role_id != desired_role.id:
                node.role = desired_role
                update_fields.append("role")
            if update_fields:
                node.save(update_fields=update_fields)
                local_registration_logger.info(
                    "Local node registration updated node_id=%s endpoint=%s address=%s",
                    node.id,
                    node.public_endpoint,
                    node.address,
                )
            else:
                node.refresh_features()
                local_registration_logger.info(
                    "Local node registration refreshed node_id=%s endpoint=%s address=%s",
                    node.id,
                    node.public_endpoint,
                    node.address,
                )
            created = False
        else:
            node = cls.objects.create(**defaults)
            created = True
            if desired_role:
                node.role = desired_role
                node.save(update_fields=["role"])
            local_registration_logger.info(
                "Local node registration created node_id=%s endpoint=%s address=%s",
                node.id,
                node.public_endpoint,
                node.address,
            )
        if created and node.role is None:
            terminal = NodeRole.objects.filter(name="Terminal").first()
            if terminal:
                node.role = terminal
                node.save(update_fields=["role"])
        node.ensure_keys()
        if notify_peers:
            node.notify_peers_of_update()
        return node, created

    def notify_peers_of_update(self):
        """Attempt to update this node's registration with known peers."""

        from secrets import token_hex

        try:
            import requests
        except Exception:  # pragma: no cover - requests should be available
            return

        security_dir = self.get_base_path() / "security"
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
        signature, error = Node.sign_payload(token, private_key)
        if not signature:
            logger.warning("Failed to sign peer update for %s: %s", self, error)
            return

        payload = {
            "hostname": self.hostname,
            "network_hostname": self.network_hostname,
            "address": self.address,
            "ipv4_address": self.ipv4_address,
            "ipv6_address": self.ipv6_address,
            "port": self.port,
            "mac_address": self.mac_address,
            "public_key": self.public_key,
            "token": token,
            "signature": signature,
        }
        if self.installed_version:
            payload["installed_version"] = self.installed_version
        if self.installed_revision:
            payload["installed_revision"] = self.installed_revision

        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        headers = {"Content-Type": "application/json"}

        peers = Node.objects.exclude(pk=self.pk)
        for peer in peers:
            host_candidates = peer.get_remote_host_candidates()
            port = peer.port or 8888
            urls: list[str] = []
            scheme_candidates = peer.iter_preferred_schemes()
            for host in host_candidates:
                host = host.strip()
                if not host:
                    continue
                if host.startswith("http://") or host.startswith("https://"):
                    parsed = urlparse(host)
                    netloc = parsed.netloc or parsed.path
                    base_path = (parsed.path or "").rstrip("/")
                    for scheme in peer.iter_preferred_schemes(
                        default=parsed.scheme or "http"
                    ):
                        candidate = urlunsplit((scheme, netloc, base_path, "", "")).rstrip(
                            "/"
                        )
                        if candidate and candidate not in urls:
                            urls.append(candidate)
                    continue
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                for scheme in scheme_candidates:
                    scheme_default_port = 443 if scheme == "https" else 80
                    if port in {80, 443} and port != scheme_default_port:
                        scheme_port = None
                    else:
                        scheme_port = port
                    if scheme_port and scheme_port != scheme_default_port:
                        candidate = f"{scheme}://{host}:{scheme_port}/nodes/register/"
                    else:
                        candidate = f"{scheme}://{host}/nodes/register/"
                    if candidate not in urls:
                        urls.append(candidate)
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
        security_dir = self.get_base_path() / "security"
        security_dir.mkdir(parents=True, exist_ok=True)
        priv_path = security_dir / f"{self.public_endpoint}"
        pub_path = security_dir / f"{self.public_endpoint}.pub"
        regenerate = not priv_path.exists() or not pub_path.exists()
        if not regenerate:
            key_max_age = getattr(settings, "NODE_KEY_MAX_AGE", timedelta(days=90))
            if key_max_age is not None:
                try:
                    priv_mtime = datetime.fromtimestamp(
                        priv_path.stat().st_mtime, tz=datetime_timezone.utc
                    )
                    pub_mtime = datetime.fromtimestamp(
                        pub_path.stat().st_mtime, tz=datetime_timezone.utc
                    )
                except OSError:
                    regenerate = True
                else:
                    cutoff = timezone.now() - key_max_age
                    if priv_mtime < cutoff or pub_mtime < cutoff:
                        regenerate = True
        if regenerate:
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
            public_text = public_bytes.decode()
            if self.public_key != public_text:
                self.public_key = public_text
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
        priv_path = self.get_base_path() / "security" / f"{self.public_endpoint}"
        try:
            return serialization.load_pem_private_key(
                priv_path.read_bytes(), password=None
            )
        except Exception:
            return None

    @staticmethod
    def sign_payload(
        payload: str, private_key
    ) -> tuple[str | None, str | None]:
        """Sign ``payload`` with ``private_key`` and return a base64 signature.

        Returns a tuple of ``(signature, error_message)``. The signature is a
        base64-encoded string that preserves padding. When signing fails, the
        error message describes the failure and ``None`` is returned for the
        signature.
        """

        if not private_key:
            return None, "Private key unavailable"

        try:
            signature = private_key.sign(
                payload.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to sign payload: %s", exc)
            return None, str(exc)

        return base64.b64encode(signature).decode("ascii"), None

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
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if self.pk:
            if is_new:
                self._apply_role_manual_features()
            self.refresh_features()

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
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="UTC timestamp after which this message should be discarded.",
    )
    lcd_channel_type = models.CharField(
        max_length=20,
        blank=True,
        default=LcdChannel.LOW.value,
        help_text="LCD channel type for local display (for example low, high, clock, or uptime).",
    )
    lcd_channel_num = models.PositiveSmallIntegerField(
        default=0,
        help_text="LCD channel number to target when displaying locally.",
    )
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
        expires_at: datetime | str | None = None,
        lcd_channel_type: str | None = None,
        lcd_channel_num: int | None = None,
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
        normalized_channel_type, normalized_channel_num = cls.normalize_lcd_channel(
            lcd_channel_type, lcd_channel_num
        )
        normalized_attachments = cls.normalize_attachments(attachments)
        msg = cls.objects.create(
            subject=subject[:64],
            body=body[:256],
            reach=role,
            node_origin=origin,
            attachments=normalized_attachments or None,
            expires_at=cls.normalize_expires_at(expires_at),
            lcd_channel_type=normalized_channel_type,
            lcd_channel_num=normalized_channel_num,
        )
        if normalized_attachments:
            msg.apply_attachments(normalized_attachments)
        msg.notify_slack()
        msg.propagate(seen=seen or [])
        return msg

    def notify_slack(self):
        """Send this Net Message to any Slack chatbots owned by the origin node."""

        try:
            SlackBotProfile = apps.get_model("teams", "SlackBotProfile")
        except (LookupError, ValueError):
            return
        if SlackBotProfile is None:
            return

        origin = self.node_origin
        if origin is None:
            origin = Node.get_local()
        if not origin:
            return

        try:
            bots = SlackBotProfile.objects.filter(node=origin, is_enabled=True)
        except Exception:  # pragma: no cover - database errors surfaced in logs
            logger.exception(
                "Failed to load Slack chatbots for node %s", getattr(origin, "pk", None)
            )
            return

        for bot in bots:
            try:
                bot.broadcast_net_message(self)
            except Exception:  # pragma: no cover - network errors logged for diagnosis
                logger.exception(
                    "Slack bot %s failed to broadcast NetMessage %s",
                    getattr(bot, "pk", None),
                    getattr(self, "pk", None),
                )

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

    @staticmethod
    def normalize_expires_at(value: datetime | str | None) -> datetime | None:
        if not value:
            return None

        parsed: datetime | None
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value))
            except ValueError:
                return None

        if timezone.is_naive(parsed):
            try:
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            except Exception:
                return None

        return parsed

    @staticmethod
    def normalize_lcd_channel(
        channel_type: object | None, channel_num: object | None
    ) -> tuple[str, int]:
        normalized_type = (
            str(channel_type or LcdChannel.LOW.value).strip() or LcdChannel.LOW.value
        ).lower()
        try:
            normalized_num = int(channel_num) if channel_num is not None else 0
        except (TypeError, ValueError):
            normalized_num = 0
        if normalized_num < 0:
            normalized_num = 0
        return normalized_type[:20], normalized_num

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return self.expires_at <= timezone.now()

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
        from apps.sigils.sigil_resolver import resolve_sigils

        payload: dict[str, object] = {
            "uuid": str(self.uuid),
            "subject": resolve_sigils(self.subject or "", current=self.node_origin),
            "body": resolve_sigils(self.body or "", current=self.node_origin),
            "seen": list(seen),
            "reach": reach_name,
            "sender": sender_id,
            "origin": origin_uuid,
        }
        channel_type, channel_num = self.normalize_lcd_channel(
            self.lcd_channel_type, self.lcd_channel_num
        )
        payload["lcd_channel_type"] = channel_type
        payload["lcd_channel_num"] = channel_num
        if self.attachments:
            payload["attachments"] = self.attachments
        if self.expires_at:
            payload["expires_at"] = self.expires_at.isoformat()
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
        signature, _error = Node.sign_payload(payload_json, private_key)
        return signature

    def queue_for_node(self, node: "Node", seen: list[str]) -> None:
        """Queue this message for later delivery to ``node``."""

        if node.current_relation != Node.Relation.DOWNSTREAM:
            return

        if self.is_expired:
            if not self.complete:
                self.complete = True
                if self.pk:
                    self.save(update_fields=["complete"])
            self.clear_queue_for_node(node)
            return

        now = timezone.now()
        expires_at = now + timedelta(hours=1)
        if self.expires_at:
            expires_at = min(expires_at, self.expires_at)
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
        channel_type, channel_num = cls.normalize_lcd_channel(
            data.get("lcd_channel_type"), data.get("lcd_channel_num")
        )
        expires_at = cls.normalize_expires_at(data.get("expires_at"))
        msg, created = cls.objects.get_or_create(
            uuid=msg_uuid,
            defaults={
                "subject": subject,
                "body": body,
                "reach": reach_role,
                "node_origin": origin_node,
                "attachments": attachments or None,
                "expires_at": expires_at,
                "lcd_channel_type": channel_type,
                "lcd_channel_num": channel_num,
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
            if msg.expires_at != expires_at:
                msg.expires_at = expires_at
                update_fields.append("expires_at")
            if (
                msg.lcd_channel_type != channel_type
                or msg.lcd_channel_num != channel_num
            ):
                msg.lcd_channel_type = channel_type
                msg.lcd_channel_num = channel_num
                update_fields.extend(["lcd_channel_type", "lcd_channel_num"])
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
        from apps.core.notifications import notify
        import random
        import requests

        if self.is_expired:
            if not self.complete:
                self.complete = True
                if self.pk:
                    self.save(update_fields=["complete"])
            PendingNetMessage.objects.filter(message=self).delete()
            return

        channel_type, channel_num = self.normalize_lcd_channel(
            self.lcd_channel_type, self.lcd_channel_num
        )
        displayed = notify(
            self.subject,
            self.body,
            expires_at=self.expires_at,
            channel_type=channel_type,
            channel_num=channel_num,
        )
        local = Node.get_local()
        if displayed:
            cutoff = timezone.now() - timedelta(hours=24)
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

        if _upgrade_in_progress():
            logger.info(
                "Skipping NetMessage propagation during upgrade in progress", extra={"id": self.pk}
            )
            return
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

        if getattr(settings, "NET_MESSAGE_DISABLE_PROPAGATION", False):
            if not self.complete:
                self.complete = True
                if self.pk:
                    self.save(update_fields=["complete"])
            return

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
            for url in node.iter_remote_urls("/nodes/net-message/"):
                try:
                    response = requests.post(
                        url,
                        data=payload_json,
                        headers=headers,
                        timeout=1,
                    )
                    success = bool(response.ok)
                except Exception:
                    logger.exception(
                        "Failed to propagate NetMessage %s to node %s via %s",
                        self.pk,
                        node.pk,
                        url,
                    )
                    continue
                if success:
                    break
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


class PendingNetMessage(Entity):
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
        verbose_name = "Pending Net Message"
        verbose_name_plural = "Pending Net Messages"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.message_id} → {self.node_id}"

    @property
    def is_stale(self) -> bool:
        if self.message and getattr(self.message, "is_expired", False):
            return True
        return self.stale_at <= timezone.now()


UserModel = get_user_model()


class User(UserModel):
    class Meta:
        proxy = True
        app_label = "nodes"
        verbose_name = UserModel._meta.verbose_name
        verbose_name_plural = UserModel._meta.verbose_name_plural
