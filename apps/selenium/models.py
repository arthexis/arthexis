from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable

from django.db import models
from django.db.models import Q
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from apps.core.entity import Entity, EntityManager
from apps.selenium.utils.firefox import find_firefox_binary
from apps.sigils.sigil_resolver import resolve_sigils

logger = logging.getLogger(__name__)


class SeleniumBrowserManager(EntityManager):
    def get_by_natural_key(self, name: str):  # pragma: no cover - fixture helper
        return self.get(name=name)

    def default(self) -> "SeleniumBrowser | None":
        return self.filter(is_default=True).first()


class SeleniumBrowser(Entity):
    class Engine(models.TextChoices):
        FIREFOX = "firefox", _("Firefox")

    class Mode(models.TextChoices):
        HEADED = "headed", _("Headed")
        HEADLESS = "headless", _("Headless")

    name = models.CharField(max_length=100, unique=True)
    engine = models.CharField(
        max_length=20, choices=Engine.choices, default=Engine.FIREFOX
    )
    mode = models.CharField(
        max_length=20, choices=Mode.choices, default=Mode.HEADED
    )
    binary_path = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Optional browser binary override."),
    )
    is_default = models.BooleanField(default=False)

    objects = SeleniumBrowserManager()

    class Meta:
        verbose_name = _("Selenium Browser")
        verbose_name_plural = _("Selenium Browsers")
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="selenium_browser_single_default",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def natural_key(self):  # pragma: no cover - fixture helper
        return (self.name,)

    @classmethod
    def default(cls) -> "SeleniumBrowser | None":
        return cls.objects.default()

    def _headless_mode(self) -> bool:
        """Return whether the browser should run in headless mode."""

        mode = self.mode
        if mode == self.Mode.HEADED and not os.environ.get("DISPLAY"):
            logger.warning("DISPLAY not set; forcing headless mode for %s", self)
            return True
        return mode == self.Mode.HEADLESS

    def _build_launch_kwargs(self) -> dict[str, object]:
        """Build Playwright launch keyword arguments for this browser."""

        launch_kwargs: dict[str, object] = {"headless": self._headless_mode()}
        binary = find_firefox_binary(self.binary_path)
        if binary:
            launch_kwargs["executable_path"] = binary
        return launch_kwargs

    def create_driver(self):
        if self.engine != self.Engine.FIREFOX:
            raise RuntimeError(f"Unsupported browser engine: {self.engine}")

        launch_kwargs = self._build_launch_kwargs()
        return PlaywrightDriver.launch(
            browser_factory=lambda p: p.firefox.launch(**launch_kwargs)
        )


class PlaywrightDriver:
    """Small Selenium-compatible adapter over Playwright page actions."""

    def __init__(
        self,
        *,
        playwright: Playwright,
        browser: Browser,
        context: BrowserContext,
        page: Page,
    ) -> None:
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self.page = page

    @classmethod
    def launch(cls, *, browser_factory: Callable[[Playwright], Browser]) -> "PlaywrightDriver":
        """Launch a Playwright browser and return a driver adapter."""

        playwright = sync_playwright().start()
        try:
            browser = browser_factory(playwright)
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            page = context.new_page()
            return cls(
                playwright=playwright,
                browser=browser,
                context=context,
                page=page,
            )
        except Exception:
            with contextlib.suppress(Exception):
                playwright.stop()
            raise

    def set_window_size(self, width: int, height: int) -> None:
        """Set the viewport size for the active Playwright page."""

        self.page.set_viewport_size({"width": width, "height": height})

    def get(self, url: str) -> None:
        """Navigate the current page to ``url``."""

        self.page.goto(url, wait_until="domcontentloaded")

    def add_cookie(self, cookie: dict) -> None:
        """Add a cookie dictionary using Playwright's context API."""

        normalized = dict(cookie)
        if "sameSite" not in normalized and normalized.get("sameSite") is None:
            normalized.pop("sameSite", None)
        if "url" not in normalized and "domain" not in normalized:
            current_url = self.page.url
            if not current_url:
                raise ValueError("Cookie requires either a domain or an active page URL.")
            normalized["url"] = current_url
        self._context.add_cookies([normalized])

    def save_screenshot(self, path: str) -> bool:
        """Save the current page screenshot and return success state."""

        self.page.screenshot(path=path, full_page=True)
        return True

    def quit(self) -> None:
        """Close all Playwright resources owned by this adapter."""

        with contextlib.suppress(Exception):
            self._context.close()
        with contextlib.suppress(Exception):
            self._browser.close()
        with contextlib.suppress(Exception):
            self._playwright.stop()


class SeleniumScript(Entity):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    start_url = models.URLField(blank=True)
    script = models.TextField(
        blank=True,
        help_text=_(
            "Inline Python to execute with the browser available as `browser`. "
            "Sigils are resolved before execution."
        ),
    )
    python_path = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Dotted path to a Python callable that accepts the browser."),
    )

    objects = EntityManager()

    class Meta:
        verbose_name = _("Selenium Script")
        verbose_name_plural = _("Selenium Scripts")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def natural_key(self):  # pragma: no cover - fixture helper
        return (self.name,)

    def _resolve_text(self, value: str, current=None) -> str:
        return resolve_sigils(value, current) if value else ""

    def _split_script(self, current=None) -> tuple[str, str]:
        resolved_script = self._resolve_text(self.script, current)
        start_url = self._resolve_text(self.start_url, current)
        body = resolved_script.strip()
        lines = resolved_script.splitlines()
        for idx, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith(("http://", "https://")):
                if not start_url:
                    start_url = stripped
                body = "\n".join(lines[idx + 1 :]).strip()
            break
        return start_url, body

    def _load_callable(self, current=None):
        python_path = self._resolve_text(self.python_path, current)
        if not python_path:
            return None
        try:
            return import_string(python_path)
        except Exception:
            logger.exception("Unable to import callable %s", python_path)
            raise

    def execute(self, browser: SeleniumBrowser | None = None, *, current=None):
        active_browser = browser or SeleniumBrowser.default()
        if active_browser is None:
            raise RuntimeError("No default Selenium browser is configured.")

        driver = active_browser.create_driver()
        try:
            start_url, body = self._split_script(current=current)
            if start_url:
                driver.get(start_url)

            callback = self._load_callable(current=current)
            if callback is not None:
                callback(driver, script=self)
                return

            if body:
                exec_globals = {
                    "browser": driver,
                    "driver": driver,
                    "page": getattr(driver, "page", None),
                    "script": self,
                }
                compiled = compile(body, f"<SeleniumScript {self.name}>", "exec")
                exec(compiled, exec_globals, exec_globals)
        finally:
            with contextlib.suppress(Exception):
                driver.quit()
