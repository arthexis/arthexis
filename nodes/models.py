from django.db import models
from core.entity import Entity
import re
from django.utils.text import slugify
from django.conf import settings
from django.contrib.sites.models import Site
import uuid
import os
import socket
from pathlib import Path
from utils import revision
from django.db import models
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization


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
            node.save(update_fields=list(defaults.keys()))
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
        if self.node_id is None:
            self.node = Node.get_local()
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return str(self.name)


class NodeCommand(Entity):
    """Shell command that can be executed on nodes."""

    command = models.TextField()
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.command

    def run(self, node: Node):
        """Execute this command on ``node`` and return its output."""
        if not node.is_local:
            raise NotImplementedError("Remote node execution is not implemented")
        import subprocess

        result = subprocess.run(
            self.command, shell=True, capture_output=True, text=True
        )
        return result.stdout + result.stderr


class Recipe(Entity):
    """A collection of script steps that can be executed by nodes."""

    name = models.CharField(max_length=100)
    full_script = models.TextField(blank=True)

    def __str__(self):  # pragma: no cover - simple representation
        return self.name

    def sync_full_script(self):
        """Update ``full_script`` to match the joined step scripts."""
        steps = self.steps.order_by("order").values_list("script", flat=True)
        self.full_script = "\n".join(steps)
        super().save(update_fields=["full_script"])


class Step(Entity):
    """Individual step belonging to a :class:`Recipe`."""

    recipe = models.ForeignKey(
        Recipe, related_name="steps", on_delete=models.CASCADE
    )
    order = models.PositiveIntegerField()
    script = models.TextField()

    class Meta:
        ordering = ["order"]

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.order}: {self.script[:30]}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.recipe.sync_full_script()

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        self.recipe.sync_full_script()




class TextPattern(Entity):
    """Text mask with optional sigils used to match against text content samples."""

    mask = models.TextField()
    priority = models.IntegerField(default=0)

    class Meta:
        ordering = ["-priority", "id"]

    SIGIL_RE = re.compile(r"\[(.+?)\]")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.mask

    def match(self, text: str):
        """Return the mask with sigils replaced if ``text`` matches it.

        ``None`` is returned when no match is found. When a match occurs, the
        returned string is the original mask with each ``[sigil]`` replaced by the
        corresponding text from ``text``. Multiple sigils are supported.
        """

        regex, names = self._compile_regex()
        match = re.search(regex, text, re.DOTALL)
        if not match:
            return None
        result = self.mask
        for name, value in zip(names, match.groups()):
            result = result.replace(f"[{name}]", value)
        return result

    def _compile_regex(self):
        """Compile the mask into a regex pattern and return pattern and sigils."""

        pattern_parts = []
        sigil_names = []
        last_index = 0
        matches = list(self.SIGIL_RE.finditer(self.mask))
        for idx, match in enumerate(matches):
            pattern_parts.append(re.escape(self.mask[last_index : match.start()]))
            sigil_names.append(match.group(1))
            part = "(.*)" if idx == len(matches) - 1 else "(.*?)"
            pattern_parts.append(part)
            last_index = match.end()
        pattern_parts.append(re.escape(self.mask[last_index:]))
        regex = "".join(pattern_parts)
        return regex, sigil_names


class Backup(Entity):
    """Database backup metadata.

    Stores the location of the exported data, creation date, size and a
    report of the exported objects.  The actual backup process is left to
    the view or task that creates the model instance, keeping the model
    backend agnostic.
    """

    location = models.CharField(
        max_length=255, help_text="Location or link to the backup file"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    size = models.BigIntegerField(help_text="Size of the backup in bytes")
    report = models.JSONField(
        default=dict,
        blank=True,
        help_text="Report of exported objects",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.location


