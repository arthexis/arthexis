from __future__ import annotations

import textwrap

from django.db import migrations


def _public_site_script() -> str:
    """Return the Playwright-compatible public site screenshot script."""

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

        from apps.content.utils import save_screenshot
        from apps.nodes.models import Node

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
    """Return the Playwright-compatible admin login screenshot script."""

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

        from apps.content.utils import save_screenshot
        from apps.nodes.models import Node

        browser.set_window_size(1280, 720)
        browser.get(admin_url)

        page.fill("input[name='username']", "admin")
        page.fill("input[name='password']", "admin")
        page.click("form input[type='submit']")
        page.wait_for_selector("body")

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


def _selenium_admin_site_script() -> str:
    """Return the previous Selenium-based admin script for reverse migration."""

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


def _selenium_public_site_script() -> str:
    """Return the previous Selenium-based public script for reverse migration."""

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
            method="SELENIUM:Public Site Test",
            link_duplicates=True,
        )
        """
    ).strip()


def apply_playwright_defaults(apps, schema_editor):
    """Write Playwright-compatible script defaults."""

    SeleniumScript = apps.get_model("selenium", "SeleniumScript")
    SeleniumScript.objects.filter(name="Public Site Test").update(
        script=_public_site_script()
    )
    SeleniumScript.objects.filter(name="Admin Site Test").update(
        script=_admin_site_script()
    )


def revert_to_selenium_defaults(apps, schema_editor):
    """Restore Selenium-oriented script defaults."""

    SeleniumScript = apps.get_model("selenium", "SeleniumScript")
    SeleniumScript.objects.filter(name="Public Site Test").update(
        script=_selenium_public_site_script()
    )
    SeleniumScript.objects.filter(name="Admin Site Test").update(
        script=_selenium_admin_site_script()
    )


class Migration(migrations.Migration):
    dependencies = [
        ("selenium", "0003_update_default_scripts"),
    ]

    operations = [
        migrations.RunPython(apply_playwright_defaults, revert_to_selenium_defaults),
    ]
