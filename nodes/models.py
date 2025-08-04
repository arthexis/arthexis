from django.db import models
import socket


class Node(models.Model):
    """Information about a running node in the network."""

    hostname = models.CharField(max_length=100)
    address = models.GenericIPAddressField()
    port = models.PositiveIntegerField(default=8000)
    badge_color = models.CharField(max_length=7, default="#28a745")
    last_seen = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.hostname}:{self.port}"


class NodeScreenshot(models.Model):
    """Screenshot captured from a node."""

    node = models.ForeignKey(
        Node, on_delete=models.SET_NULL, null=True, blank=True
    )
    path = models.CharField(max_length=255)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.path


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
