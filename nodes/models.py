from collections.abc import Iterable
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from core.entity import Entity
from core.fields import SigilShortAutoField
import re
import json
import base64
from django.utils.text import slugify
from django.conf import settings
from django.contrib.sites.models import Site
import uuid
import os
import socket
from pathlib import Path
from utils import revision
from django.core.exceptions import ValidationError
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from django.contrib.auth import get_user_model
from django.core.mail import get_connection
from core import mailer
import logging


logger = logging.getLogger(__name__)


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


class NodeFeature(Entity):
    """Feature that may be enabled on nodes and roles."""

    slug = models.SlugField(max_length=50, unique=True)
    display = models.CharField(max_length=50)
    roles = models.ManyToManyField(NodeRole, blank=True, related_name="features")

    objects = NodeFeatureManager()

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
        if self.slug == "gui-toast":
            from core.notifications import supports_gui_toast

            return supports_gui_toast()
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
        if node.role:
            return self.roles.filter(pk=node.role.pk).exists()
        return False


def get_terminal_role():
    """Return the NodeRole representing a Terminal if it exists."""
    return NodeRole.objects.filter(name="Terminal").first()


class Node(Entity):
    """Information about a running node in the network."""

    hostname = models.CharField(max_length=100)
    address = models.GenericIPAddressField()
    mac_address = models.CharField(max_length=17, unique=True, null=True, blank=True)
    port = models.PositiveIntegerField(default=8000)
    badge_color = models.CharField(max_length=7, default="#28a745")
    role = models.ForeignKey(NodeRole, on_delete=models.SET_NULL, null=True, blank=True)
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
    AUTO_MANAGED_FEATURES = set(FEATURE_LOCK_MAP.keys()) | {"gui-toast"}
    MANUAL_FEATURE_SLUGS = {"clipboard-poll", "screenshot-poll"}

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.hostname}:{self.port}"

    @staticmethod
    def get_current_mac() -> str:
        """Return the MAC address of the current host."""
        return ":".join(re.findall("..", f"{uuid.getnode():012x}"))

    @classmethod
    def get_local(cls):
        """Return the node representing the current host if it exists."""
        mac = cls.get_current_mac()
        return cls.objects.filter(mac_address=mac).first()

    @classmethod
    def register_current(cls):
        """Create or update the :class:`Node` entry for this host."""
        hostname = socket.gethostname()
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
        slug = slugify(hostname)
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
        }
        if node:
            for field, value in defaults.items():
                setattr(node, field, value)
            update_fields = list(defaults.keys())
            node.save(update_fields=update_fields)
            created = False
        else:
            node = cls.objects.create(**defaults)
            created = True
            # assign role from installation lock file
            role_lock = Path(settings.BASE_DIR) / "locks" / "role.lck"
            role_name = (
                role_lock.read_text().strip() if role_lock.exists() else "Terminal"
            )
            role = NodeRole.objects.filter(name=role_name).first()
            if role:
                node.role = role
                node.save(update_fields=["role"])
        if created and node.role is None:
            terminal = NodeRole.objects.filter(name="Terminal").first()
            if terminal:
                node.role = terminal
                node.save(update_fields=["role"])
        Site.objects.get_or_create(domain=hostname, defaults={"name": "host"})
        node.ensure_keys()
        return node, created

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

    @property
    def is_local(self):
        """Determine if this node represents the current host."""
        return self.mac_address == self.get_current_mac()

    def save(self, *args, **kwargs):
        if self.mac_address:
            self.mac_address = self.mac_address.lower()
        if not self.public_endpoint:
            self.public_endpoint = slugify(self.hostname)
        super().save(*args, **kwargs)
        if self.pk:
            self.refresh_features()

    def has_feature(self, slug: str) -> bool:
        return self.features.filter(slug=slug).exists()

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
        self._sync_clipboard_task(clipboard_enabled)
        self._sync_screenshot_task(screenshot_enabled)

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


class NodeFeatureAssignment(models.Model):
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


class EmailOutbox(Entity):
    """SMTP credentials for sending mail."""

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

    class Meta:
        verbose_name = "Email Outbox"
        verbose_name_plural = "Email Outboxes"

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


class NetMessage(Entity):
    """Message propagated across nodes."""

    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="UUID",
    )
    subject = models.CharField(max_length=64, blank=True)
    body = models.CharField(max_length=256, blank=True)
    reach = models.ForeignKey(
        NodeRole,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        default=get_terminal_role,
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
    ):
        role = None
        if reach:
            if isinstance(reach, NodeRole):
                role = reach
            else:
                role = NodeRole.objects.filter(name=reach).first()
        msg = cls.objects.create(
            subject=subject[:64],
            body=body[:256],
            reach=role or get_terminal_role(),
        )
        msg.propagate(seen=seen or [])
        return msg

    def propagate(self, seen: list[str] | None = None):
        from core.notifications import notify
        import random
        import requests

        notify(self.subject, self.body)
        local = Node.get_local()
        private_key = None
        seen = list(seen or [])
        local_id = None
        if local:
            local_id = str(local.uuid)
            if local_id not in seen:
                seen.append(local_id)
            priv_path = (
                Path(local.base_path or settings.BASE_DIR)
                / "security"
                / f"{local.public_endpoint}"
            )
            try:
                private_key = serialization.load_pem_private_key(
                    priv_path.read_bytes(), password=None
                )
            except Exception:
                private_key = None
        for node_id in seen:
            node = Node.objects.filter(uuid=node_id).first()
            if node and (not local or node.pk != local.pk):
                self.propagated_to.add(node)

        all_nodes = Node.objects.all()
        if local:
            all_nodes = all_nodes.exclude(pk=local.pk)
        total_known = all_nodes.count()

        remaining = list(
            all_nodes.exclude(pk__in=self.propagated_to.values_list("pk", flat=True))
        )
        if not remaining:
            self.complete = True
            self.save(update_fields=["complete"])
            return

        target_limit = min(3, len(remaining))

        reach_name = self.reach.name if self.reach else "Terminal"
        role_map = {
            "Terminal": ["Terminal"],
            "Control": ["Control", "Terminal"],
            "Satellite": ["Satellite", "Control", "Terminal"],
            "Constellation": [
                "Constellation",
                "Satellite",
                "Control",
                "Terminal",
            ],
        }
        role_order = role_map.get(reach_name, ["Terminal"])
        selected: list[Node] = []
        for role_name in role_order:
            role_nodes = [n for n in remaining if n.role and n.role.name == role_name]
            random.shuffle(role_nodes)
            for n in role_nodes:
                selected.append(n)
                remaining.remove(n)
                if len(selected) >= target_limit:
                    break
            if len(selected) >= target_limit:
                break

        seen_list = seen.copy()
        selected_ids = [str(n.uuid) for n in selected]
        payload_seen = seen_list + selected_ids
        for node in selected:
            payload = {
                "uuid": str(self.uuid),
                "subject": self.subject,
                "body": self.body,
                "seen": payload_seen,
                "reach": reach_name,
                "sender": local_id,
            }
            payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            headers = {"Content-Type": "application/json"}
            if private_key:
                try:
                    signature = private_key.sign(
                        payload_json.encode(),
                        padding.PKCS1v15(),
                        hashes.SHA256(),
                    )
                    headers["X-Signature"] = base64.b64encode(signature).decode()
                except Exception:
                    pass
            try:
                requests.post(
                    f"http://{node.address}:{node.port}/nodes/net-message/",
                    data=payload_json,
                    headers=headers,
                    timeout=1,
                )
            except Exception:
                pass
            self.propagated_to.add(node)

        if total_known and self.propagated_to.count() >= total_known:
            self.complete = True
        self.save(update_fields=["complete"] if self.complete else [])


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


UserModel = get_user_model()


class User(UserModel):
    class Meta:
        proxy = True
        app_label = "nodes"
        verbose_name = UserModel._meta.verbose_name
        verbose_name_plural = UserModel._meta.verbose_name_plural
