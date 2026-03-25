from __future__ import annotations

import textwrap

from django.db import migrations


def _public_site_script() -> str:
    return textwrap.dedent(
        """
        target_host = "[NODE.get_primary_contact]"
        port = "[NODE.port]"

        def normalize_host(value: str, fallback: str = "localhost") -> str:
            value = (value or "").strip()
            if not value:
                return fallback
            if value.startswith("[") and value.endswith("]"):
                return fallback
            return value

        def normalize_port(value: str, fallback: str = "8888") -> str:
            value = str(value or "").strip()
            if not value:
                return fallback
            if value.startswith("[") and value.endswith("]"):
                return fallback
            return value

        def build_base_url(host: str, port: str) -> str:
            if host.startswith(("http://", "https://")):
                return host
            if port:
                return f"http://{host}:{port}"
            return f"http://{host}"

        host = normalize_host(target_host)
        port = normalize_port(port)
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
            method="SELENIUM:Public Site Test",
            link_duplicates=True,
        )
        """
    ).strip()


def _admin_site_script() -> str:
    return textwrap.dedent(
        """
        target_host = "[NODE.get_primary_contact]"
        port = "[NODE.port]"

        def normalize_host(value: str, fallback: str = "localhost") -> str:
            value = (value or "").strip()
            if not value:
                return fallback
            if value.startswith("[") and value.endswith("]"):
                return fallback
            return value

        def normalize_port(value: str, fallback: str = "8888") -> str:
            value = str(value or "").strip()
            if not value:
                return fallback
            if value.startswith("[") and value.endswith("]"):
                return fallback
            return value

        def build_base_url(host: str, port: str) -> str:
            if host.startswith(("http://", "https://")):
                return host
            if port:
                return f"http://{host}:{port}"
            return f"http://{host}"

        host = normalize_host(target_host)
        port = normalize_port(port)
        base_url = build_base_url(host, port).rstrip("/")
        admin_url = f"{base_url}/admin/"

        from pathlib import Path
        from uuid import uuid4

        from django.conf import settings
        from django.utils import timezone
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        from apps.nodes.models import Node
        from apps.content.utils import save_screenshot

        browser.set_window_size(1280, 720)
        browser.get(admin_url)

        wait = WebDriverWait(browser, 10)
        username_input = wait.until(EC.presence_of_element_located((By.NAME, "username")))
        password_input = wait.until(EC.presence_of_element_located((By.NAME, "password")))

        username_input.clear()
        username_input.send_keys("admin")
        password_input.clear()
        password_input.send_keys("admin")

        submit = browser.find_element(By.CSS_SELECTOR, "form input[type='submit']")
        submit.click()

        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

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
            method="SELENIUM:Admin Site Test",
            link_duplicates=True,
        )
        """
    ).strip()


def create_scripts(apps, schema_editor):
    SeleniumScript = apps.get_model("selenium", "SeleniumScript")

    scripts = [
        {
            "name": "Public Site Test",
            "description": "Capture a screenshot of the node's public site and store it as content.",
            "script": _public_site_script(),
        },
        {
            "name": "Admin Site Test",
            "description": "Login to the admin with default credentials and store a screenshot.",
            "script": _admin_site_script(),
        },
    ]

    for payload in scripts:
        SeleniumScript.objects.update_or_create(
            name=payload["name"], defaults=payload
        )


def remove_scripts(apps, schema_editor):
    SeleniumScript = apps.get_model("selenium", "SeleniumScript")
    SeleniumScript.objects.filter(
        name__in=("Public Site Test", "Admin Site Test")
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("selenium", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_scripts, remove_scripts),
    ]
