from django.db import models
from core.entity import Entity
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
from django.core.mail import get_connection, send_mail


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


class Node(Entity):
    """Information about a running node in the network."""

    hostname = models.CharField(max_length=100)
    address = models.GenericIPAddressField()
    mac_address = models.CharField(
        max_length=17, unique=True, null=True, blank=True
    )
    port = models.PositiveIntegerField(default=8000)
    badge_color = models.CharField(max_length=7, default="#28a745")
    role = models.ForeignKey(NodeRole, on_delete=models.SET_NULL, null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)
    enable_public_api = models.BooleanField(default=False)
    public_endpoint = models.SlugField(blank=True, unique=True)
    clipboard_polling = models.BooleanField(default=False)
    screenshot_polling = models.BooleanField(default=False)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    public_key = models.TextField(blank=True)
    base_path = models.CharField(max_length=255, blank=True)
    installed_version = models.CharField(max_length=20, blank=True)
    installed_revision = models.CharField(max_length=40, blank=True)
    has_lcd_screen = models.BooleanField(default=False)

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
        lcd_lock = Path(settings.BASE_DIR) / "locks" / "lcd_screen.lck"
        defaults = {
            "hostname": hostname,
            "address": address,
            "port": port,
            "base_path": base_path,
            "installed_version": installed_version,
            "installed_revision": installed_revision,
            "public_endpoint": slug,
            "mac_address": mac,
            "has_lcd_screen": lcd_lock.exists(),
        }
        if node:
            for field, value in defaults.items():
                if field == "has_lcd_screen":
                    continue
                setattr(node, field, value)
            update_fields = [k for k in defaults.keys() if k != "has_lcd_screen"]
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
            private_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048
            )
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
        previous_clipboard = previous_screenshot = None
        if self.pk:
            previous = Node.objects.get(pk=self.pk)
            previous_clipboard = previous.clipboard_polling
            previous_screenshot = previous.screenshot_polling
        super().save(*args, **kwargs)
        if previous_clipboard != self.clipboard_polling:
            self._sync_clipboard_task()
        if previous_screenshot != self.screenshot_polling:
            self._sync_screenshot_task()

    def _sync_clipboard_task(self):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        task_name = f"poll_clipboard_node_{self.pk}"
        if self.clipboard_polling:
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

    def _sync_screenshot_task(self):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
        import json

        task_name = f"capture_screenshot_node_{self.pk}"
        if self.screenshot_polling:
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


    def send_mail(self, subject: str, message: str, recipient_list: list[str], from_email: str | None = None, **kwargs):
        """Send an email using this node's configured outbox if available."""
        outbox = getattr(self, "email_outbox", None)
        if outbox:
            return outbox.send_mail(subject, message, recipient_list, from_email, **kwargs)
        from_email = from_email or settings.DEFAULT_FROM_EMAIL
        return send_mail(subject, message, from_email, recipient_list, **kwargs)


class EmailOutbox(Entity):
    """SMTP credentials for sending mail from a node."""

    node = models.OneToOneField(
        Node, on_delete=models.CASCADE, related_name="email_outbox"
    )
    host = models.CharField(max_length=100)
    port = models.PositiveIntegerField(default=587)
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=100, blank=True)
    use_tls = models.BooleanField(default=True)
    use_ssl = models.BooleanField(default=False)
    from_email = models.EmailField(blank=True)

    class Meta:
        verbose_name = "Email Outbox"
        verbose_name_plural = "Email Outboxes"

    def get_connection(self):
        return get_connection(
            host=self.host,
            port=self.port,
            username=self.username or None,
            password=self.password or None,
            use_tls=self.use_tls,
            use_ssl=self.use_ssl,
        )

    def send_mail(self, subject, message, recipient_list, from_email=None, **kwargs):
        connection = self.get_connection()
        from_email = from_email or self.from_email or settings.DEFAULT_FROM_EMAIL
        return send_mail(
            subject,
            message,
            from_email,
            recipient_list,
            connection=connection,
            **kwargs,
        )


class NetMessage(Entity):
    """Message propagated across nodes."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    subject = models.CharField(max_length=64, blank=True)
    body = models.CharField(max_length=256, blank=True)
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
    def broadcast(cls, subject: str, body: str, seen: list[str] | None = None):
        msg = cls.objects.create(subject=subject[:64], body=body[:256])
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
        target_limit = total_known if total_known < 2 else 3

        if self.propagated_to.count() >= target_limit:
            self.complete = True
            self.save(update_fields=["complete"])
            return

        remaining = list(
            all_nodes.exclude(pk__in=self.propagated_to.values_list("pk", flat=True))
        )
        if not remaining:
            self.complete = True
            self.save(update_fields=["complete"])
            return

        role_order = ["Control", "Constellation", "Satellite", "Terminal"]
        selected: list[Node] = []
        for role_name in role_order:
            role_nodes = [n for n in remaining if n.role and n.role.name == role_name]
            random.shuffle(role_nodes)
            for n in role_nodes:
                selected.append(n)
                remaining.remove(n)
                if len(selected) + self.propagated_to.count() >= target_limit:
                    break
            if len(selected) + self.propagated_to.count() >= target_limit:
                break
        if len(selected) + self.propagated_to.count() < target_limit:
            random.shuffle(remaining)
            for n in remaining:
                selected.append(n)
                if len(selected) + self.propagated_to.count() >= target_limit:
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

        if self.propagated_to.count() >= target_limit or (
            total_known and self.propagated_to.count() >= total_known
        ):
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
        default=uuid.uuid4, editable=True, db_index=True
    )
    node = models.ForeignKey(
        Node, on_delete=models.SET_NULL, null=True, blank=True
    )
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


class NodeTask(Entity):
    """Script that can be executed on nodes."""

    recipe = models.TextField()
    role = models.ForeignKey(NodeRole, on_delete=models.SET_NULL, null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        verbose_name = "Node Task"
        verbose_name_plural = "Node Tasks"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.recipe

    def run(self, node: Node):
        """Execute this script on ``node`` and return its output."""
        if not node.is_local:
            raise NotImplementedError("Remote node execution is not implemented")
        import subprocess

        result = subprocess.run(
            self.recipe, shell=True, capture_output=True, text=True
        )
        return result.stdout + result.stderr


UserModel = get_user_model()


class User(UserModel):
    class Meta:
        proxy = True
        app_label = "nodes"
        verbose_name = UserModel._meta.verbose_name
        verbose_name_plural = UserModel._meta.verbose_name_plural




