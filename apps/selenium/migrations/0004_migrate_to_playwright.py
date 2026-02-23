from __future__ import annotations

import textwrap

from django.db import migrations, models


def _public_site_script() -> str:
    return textwrap.dedent(
        """
        from apps.sigils import resolve

        host = resolve("[NODE.get_primary_contact]", default="localhost")
        port = resolve("[NODE.port]", default="8888")

        def build_base_url(host: str, port: str) -> str:
            if host.startswith(("http://", "https://")):
                return host
            if port:
                return f"http://{host}:{port}"
            return f"http://{host}"

        base_url = build_base_url(host, port)

        browser.set_window_size(1280, 720)
        browser.get(base_url)

        from pathlib import Path
        from uuid import uuid4

        from django.conf import settings
        from django.utils import timezone

        from apps.nodes.models import Node
        from apps.content.utils import save_screenshot

        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        filename = (
            Path(settings.LOG_DIR)
            / "screenshots"
            / f"public-site-{timestamp}-{uuid4().hex}.png"
        )
        filename.parent.mkdir(parents=True, exist_ok=True)

        browser.save_screenshot(str(filename))

        node = Node.objects.filter(pk="[NODE.pk]").first()
        save_screenshot(
            filename,
            node=node,
            method="PLAYWRIGHT:Public Site Test",
            link_duplicates=True,
        )
        """
    ).strip()


def _admin_site_script() -> str:
    return textwrap.dedent(
        """
        from apps.sigils import resolve

        host = resolve("[NODE.get_primary_contact]", default="localhost")
        port = resolve("[NODE.port]", default="8888")

        def build_base_url(host: str, port: str) -> str:
            if host.startswith(("http://", "https://")):
                return host
            if port:
                return f"http://{host}:{port}"
            return f"http://{host}"

        base_url = build_base_url(host, port).rstrip("/")
        admin_url = f"{base_url}/admin/"

        from pathlib import Path
        from uuid import uuid4

        from django.conf import settings
        from django.utils import timezone

        from apps.nodes.models import Node
        from apps.content.utils import save_screenshot

        browser.set_window_size(1280, 720)
        browser.get(admin_url)

        browser.page.fill("input[name='username']", "admin")
        browser.page.fill("input[name='password']", "admin")
        browser.page.locator("form input[type='submit']").click()
        browser.page.wait_for_load_state("networkidle")

        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        filename = (
            Path(settings.LOG_DIR)
            / "screenshots"
            / f"admin-site-{timestamp}-{uuid4().hex}.png"
        )
        filename.parent.mkdir(parents=True, exist_ok=True)

        browser.save_screenshot(str(filename))

        node = Node.objects.filter(pk="[NODE.pk]").first()
        save_screenshot(
            filename,
            node=node,
            method="PLAYWRIGHT:Admin Site Test",
            link_duplicates=True,
        )
        """
    ).strip()


def forwards(apps, schema_editor):
    SeleniumBrowser = apps.get_model("selenium", "SeleniumBrowser")
    SeleniumScript = apps.get_model("selenium", "SeleniumScript")

    SeleniumBrowser.objects.filter(engine="firefox").update(engine="chromium")
    for browser in SeleniumBrowser.objects.filter(name__icontains="Firefox"):
        browser.name = browser.name.replace("Firefox", "Chromium")
        browser.save(update_fields=["name"])

    SeleniumScript.objects.update_or_create(
        name="Public Site Test",
        defaults={
            "description": "Capture a screenshot of the node's public site and store it as content.",
            "script": _public_site_script(),
        },
    )
    SeleniumScript.objects.update_or_create(
        name="Admin Site Test",
        defaults={
            "description": "Login to the admin with default credentials and store a screenshot.",
            "script": _admin_site_script(),
        },
    )


def backwards(apps, schema_editor):
    SeleniumBrowser = apps.get_model("selenium", "SeleniumBrowser")

    SeleniumBrowser.objects.filter(engine="chromium").update(engine="firefox")
    for browser in SeleniumBrowser.objects.filter(name__icontains="Chromium"):
        browser.name = browser.name.replace("Chromium", "Firefox")
        browser.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [
        ("selenium", "0003_update_default_scripts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="seleniumbrowser",
            name="engine",
            field=models.CharField(
                choices=[
                    ("chromium", "Chromium"),
                    ("firefox", "Firefox"),
                    ("webkit", "WebKit"),
                ],
                default="chromium",
                max_length=20,
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
