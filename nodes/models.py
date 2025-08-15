from django.db import models
import socket
import re
from django.utils.text import slugify
import uuid
import os


class NodeRole(models.Model):
    """Assignable role for a :class:`Node`."""

    name = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class Node(models.Model):
    """Information about a running node in the network."""

    hostname = models.CharField(max_length=100)
    address = models.GenericIPAddressField()
    port = models.PositiveIntegerField(default=8000)
    badge_color = models.CharField(max_length=7, default="#28a745")
    roles = models.ManyToManyField(NodeRole, blank=True)
    last_seen = models.DateTimeField(auto_now=True)
    enable_public_api = models.BooleanField(default=False)
    public_endpoint = models.SlugField(blank=True, unique=True)
    clipboard_polling = models.BooleanField(default=False)
    screenshot_polling = models.BooleanField(default=False)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    public_key = models.TextField(blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.hostname}:{self.port}"

    @classmethod
    def get_local(cls):
        """Return the node representing the current host if it exists."""
        hostname = socket.gethostname()
        port = int(os.environ.get("PORT", 8000))
        return cls.objects.filter(hostname=hostname, port=port).first()

    @property
    def is_local(self):
        """Determine if this node represents the current host."""
        hostname = socket.gethostname()
        port = int(os.environ.get("PORT", 8000))
        return self.hostname == hostname and self.port == port

    def save(self, *args, **kwargs):
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


class NodeScreenshot(models.Model):
    """Screenshot captured from a node."""

    node = models.ForeignKey(
        Node, on_delete=models.SET_NULL, null=True, blank=True
    )
    path = models.CharField(max_length=255)
    method = models.CharField(max_length=10, default="", blank=True)
    hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.path


class NodeMessage(models.Model):
    """Message received via a node's public API."""

    node = models.ForeignKey(
        Node, related_name="messages", on_delete=models.CASCADE
    )
    method = models.CharField(max_length=10)
    headers = models.JSONField(default=dict, blank=True)
    body = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.node} {self.method} {self.created}"


class NodeCommand(models.Model):
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


class Recipe(models.Model):
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


class Step(models.Model):
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


class TextSample(models.Model):
    """Clipboard text captured with timestamp."""

    name = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    content = models.TextField()
    node = models.ForeignKey(
        Node, on_delete=models.SET_NULL, null=True, blank=True
    )
    automated = models.BooleanField(
        default=False,
        help_text="Set to True on entries generated by an automatic process",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Text Sample"
        verbose_name_plural = "Text Samples"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return str(self.name)


class TextPattern(models.Model):
    """Text mask with optional sigils used to match against ``TextSample`` content."""

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


