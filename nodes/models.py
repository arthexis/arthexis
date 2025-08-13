from django.db import models
import socket
import re
import configparser
from django.utils.text import slugify
import uuid
import subprocess
import os
from pathlib import Path
from django.conf import settings
from django.db.models.signals import m2m_changed
from django.dispatch import receiver


class Node(models.Model):
    """Information about a running node in the network."""

    hostname = models.CharField(max_length=100)
    address = models.GenericIPAddressField()
    port = models.PositiveIntegerField(default=8000)
    badge_color = models.CharField(max_length=7, default="#28a745")
    last_seen = models.DateTimeField(auto_now=True)
    enable_public_api = models.BooleanField(default=False)
    public_endpoint = models.SlugField(blank=True, unique=True)
    clipboard_polling = models.BooleanField(default=False)
    screenshot_polling = models.BooleanField(default=False)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.hostname}:{self.port}"

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
        self._sync_nmcli_task()

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

    def _sync_nmcli_task(self):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        if os.name == "nt":
            return
        task_name = f"check_nmcli_node_{self.pk}"
        if self.required_nmcli_templates.exists():
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=2, period=IntervalSchedule.MINUTES
            )
            PeriodicTask.objects.update_or_create(
                name=task_name,
                defaults={
                    "interval": schedule,
                    "task": "nodes.tasks.check_required_connections",
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


class NginxConfig(models.Model):
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Identifier for this configuration (e.g., 'myapp')",
    )
    server_name = models.CharField(
        max_length=255,
        help_text="Host name(s) for the server block (e.g., example.com)",
    )
    primary_upstream = models.CharField(
        max_length=255,
        help_text="Primary upstream in host:port form (e.g., 10.0.0.1:8000)",
    )
    backup_upstream = models.CharField(
        max_length=255,
        blank=True,
        help_text="Backup upstream in host:port form (e.g., 127.0.0.1:9000)",
    )
    listen_port = models.PositiveIntegerField(
        default=80,
        help_text="Port nginx listens on (e.g., 80 or 443)",
    )
    ssl_certificate = models.CharField(
        max_length=255,
        blank=True,
        help_text="Path to SSL certificate (e.g., /etc/ssl/certs/example.crt)",
    )
    ssl_certificate_key = models.CharField(
        max_length=255,
        blank=True,
        help_text="Path to SSL certificate key (e.g., /etc/ssl/private/example.key)",
    )
    config_text = models.TextField(blank=True)

    class Meta:
        verbose_name = "NGINX Template"
        verbose_name_plural = "NGINX Templates"

    def __str__(self):  # pragma: no cover - simple representation
        return self.name

    def render_config(self):
        """Generate an NGINX template with websocket support and optional SSL."""
        upstream_name = f"{self.name}_upstream"
        lines = [
            f"upstream {upstream_name} {{",
            f"    server {self.primary_upstream};",
        ]
        if self.backup_upstream:
            lines.append(f"    server {self.backup_upstream} backup;")
        lines.append("}")

        if self.ssl_certificate and self.ssl_certificate_key:
            listen_line = f"    listen {self.listen_port} ssl;"
            ssl_lines = [
                f"    ssl_certificate {self.ssl_certificate};",
                f"    ssl_certificate_key {self.ssl_certificate_key};",
            ]
        else:
            listen_line = f"    listen {self.listen_port};"
            ssl_lines = []

        server_lines = [
            "server {",
            listen_line,
            f"    server_name {self.server_name};",
        ] + ssl_lines + [
            "    location / {",
            f"        proxy_pass http://{upstream_name};",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Upgrade $http_upgrade;",
            "        proxy_set_header Connection \"upgrade\";",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Real-IP $remote_addr;",
            "    }",
            "}",
        ]
        return "\n".join(lines + ["", *server_lines, ""]) + "\n"

    def save(self, *args, **kwargs):
        self.config_text = self.render_config()
        super().save(*args, **kwargs)

    def test_connection(self, timeout=3):
        """Try to resolve a connection to the primary or backup upstream."""
        for target in [self.primary_upstream, self.backup_upstream]:
            if not target:
                continue
            host, _, port = target.partition(":")
            port = int(port or 80)
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except OSError:
                continue
        return False


class NMCLITemplate(models.Model):
    connection_name = models.CharField(max_length=100, unique=True)
    assigned_device = models.CharField(max_length=100, blank=True)
    priority = models.IntegerField(default=0)
    autoconnect = models.BooleanField(default=True)
    static_ip = models.GenericIPAddressField(blank=True, null=True)
    static_mask = models.CharField(max_length=15, blank=True)
    static_gateway = models.GenericIPAddressField(blank=True, null=True)
    allow_outbound = models.BooleanField(default=True)
    security_type = models.CharField(max_length=50, blank=True)
    ssid = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=100, blank=True)
    band = models.CharField(max_length=10, blank=True)
    required_nodes = models.ManyToManyField(
        Node, related_name="required_nmcli_templates", blank=True
    )

    class Meta:
        verbose_name = "NMCLI Template"
        verbose_name_plural = "NMCLI Templates"

    def __str__(self):  # pragma: no cover - simple representation
        return self.connection_name


class SystemdUnit(models.Model):
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Identifier for this unit (e.g., 'myservice')",
    )
    description = models.CharField(max_length=255, blank=True)
    documentation = models.URLField(blank=True)
    user = models.CharField(max_length=100, blank=True)
    exec_start = models.CharField(max_length=255)
    wanted_by = models.CharField(max_length=100, default="default.target")
    config_text = models.TextField(blank=True)

    class Meta:
        verbose_name = "Systemd Unit Template"
        verbose_name_plural = "Systemd Unit Templates"

    def __str__(self):  # pragma: no cover - simple representation
        return self.name

    def render_unit(self):
        lines = [
            "[Unit]",
            f"Description={self.description}",
        ]
        if self.documentation:
            lines.append(f"Documentation={self.documentation}")
        lines += [
            "",
            "[Service]",
        ]
        if self.user:
            lines.append(f"User={self.user}")
        lines.append(f"ExecStart={self.exec_start}")
        lines += [
            "",
            "[Install]",
            f"WantedBy={self.wanted_by}",
            "",
        ]
        return "\n".join(lines)

    def save(self, *args, **kwargs):
        self.config_text = self.render_unit()
        super().save(*args, **kwargs)

    def is_installed(self):
        root = getattr(settings, "SYSTEMD_UNIT_ROOT", "/etc/systemd/system")
        return (Path(root) / f"{self.name}.service").exists()

    def is_running(self):
        try:
            subprocess.run(
                ["systemctl", "is-active", f"{self.name}.service"],
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
        else:
            return True

    @classmethod
    def parse_config(cls, name, text):
        parser = configparser.ConfigParser()
        parser.read_string(text)
        return cls(
            name=name,
            description=parser.get("Unit", "Description", fallback=""),
            documentation=parser.get("Unit", "Documentation", fallback=""),
            user=parser.get("Service", "User", fallback=""),
            exec_start=parser.get("Service", "ExecStart", fallback=""),
            wanted_by=parser.get("Install", "WantedBy", fallback="default.target"),
            config_text=text,
        )


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


@receiver(m2m_changed, sender=NMCLITemplate.required_nodes.through)
def _nmcli_required_nodes_changed(sender, instance, action, pk_set, **kwargs):
    if action in {"post_add", "post_remove"}:
        node_ids = set(pk_set or [])
        node_ids.update(instance.required_nodes.values_list("pk", flat=True))
        for node in Node.objects.filter(pk__in=node_ids):
            node._sync_nmcli_task()
    elif action == "post_clear":
        for node in Node.objects.all():
            node._sync_nmcli_task()
