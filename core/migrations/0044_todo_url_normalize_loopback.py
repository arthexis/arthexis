from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.db import migrations


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _collect_allowed_hosts(site_model) -> set[str]:
    hosts: set[str] = set()
    hosts.update(LOOPBACK_HOSTS)

    for host in getattr(settings, "ALLOWED_HOSTS", []):
        if not isinstance(host, str):
            continue
        normalized = host.strip().lower()
        if not normalized or normalized.startswith("*"):
            continue
        hosts.add(normalized)
        hosts.add(normalized.split(":", 1)[0])

    for site in site_model.objects.all().only("domain"):
        domain = (site.domain or "").strip().lower()
        if not domain:
            continue
        hosts.add(domain)
        hosts.add(domain.split(":", 1)[0])

    hosts.discard("")
    return hosts


def normalize_todo_urls(apps, schema_editor):
    Todo = apps.get_model("core", "Todo")
    Site = apps.get_model("sites", "Site")

    allowed_hosts = _collect_allowed_hosts(Site)

    queryset = Todo.objects.exclude(url__exact="").iterator()
    for todo in queryset:
        raw_url = (todo.url or "").strip()
        if not raw_url:
            continue
        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            continue

        if not parsed.scheme or parsed.scheme.lower() not in {"http", "https"}:
            continue

        hostname = (parsed.hostname or "").strip().lower()
        netloc = parsed.netloc.strip().lower()
        if hostname not in allowed_hosts and netloc not in allowed_hosts:
            continue

        path = parsed.path or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        normalized = urlunsplit(("", "", path, parsed.query, parsed.fragment)) or "/"

        if normalized != raw_url:
            Todo.objects.filter(pk=todo.pk).update(url=normalized)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_reference_show_in_header"),
    ]

    operations = [
        migrations.RunPython(normalize_todo_urls, migrations.RunPython.noop),
    ]
