from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass

from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from playwright.sync_api import Browser as PlaywrightBrowserInstance
from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from apps.core.entity import Entity, EntityManager
from apps.core.models import Ownable
from apps.sigils.sigil_resolver import resolve_sigils
from apps.selenium.playwright import normalize_playwright_cookie

logger = logging.getLogger(__name__)


class UnsupportedBrowserEngineError(ValueError):
    """Raised when a browser engine cannot be launched via Playwright."""


class InvalidCookiePayloadError(ValueError):
    """Raised when a session cookie payload does not match the expected shape."""


@dataclass
class PlaywrightDriver:
    """Small compatibility wrapper around a Playwright page/context/browser lifecycle."""

    playwright: Playwright
    browser: PlaywrightBrowserInstance
    context: BrowserContext
    page: Page

    def get(self, url: str) -> None:
        """Navigate to ``url`` and wait for network idle."""

        self.page.goto(url, wait_until="networkidle")

    def set_window_size(self, width: int, height: int) -> None:
        """Resize the viewport for screenshot workflows."""

        self.page.set_viewport_size({"width": width, "height": height})

    def add_cookie(self, cookie: dict) -> None:
        """Add one cookie mapping in the format expected by Selenium-like callers."""

        self.context.add_cookies([normalize_playwright_cookie(cookie)])

    def fill(self, selector: str, value: str) -> None:
        """Fill ``selector`` with ``value`` on the active page."""

        self.page.fill(selector, value)

    def click(self, selector: str) -> None:
        """Click ``selector`` on the active page."""

        self.page.locator(selector).click()

    def wait_for_load_state(self, state: str = "load") -> None:
        """Wait for the active page to reach the target load ``state``."""

        self.page.wait_for_load_state(state)

    def save_screenshot(self, path: str) -> bool:
        """Capture a page screenshot to ``path`` and return ``True`` when saved."""

        self.page.screenshot(path=path, full_page=True)
        return True

    def quit(self) -> None:
        """Close Playwright resources in the correct order."""

        with contextlib.suppress(Exception):
            self.context.close()
        with contextlib.suppress(Exception):
            self.browser.close()
        with contextlib.suppress(Exception):
            self.playwright.stop()


class SeleniumBrowserManager(EntityManager):
    def get_by_natural_key(self, name: str):  # pragma: no cover - fixture helper
        return self.get(name=name)

    def default(self) -> "SeleniumBrowser | None":
        return self.filter(is_default=True).first()


class SeleniumBrowser(Entity):
    class Engine(models.TextChoices):
        CHROMIUM = "chromium", _("Chromium")
        FIREFOX = "firefox", _("Firefox")
        WEBKIT = "webkit", _("WebKit")

    class Mode(models.TextChoices):
        HEADED = "headed", _("Headed")
        HEADLESS = "headless", _("Headless")

    name = models.CharField(max_length=100, unique=True)
    engine = models.CharField(
        max_length=20, choices=Engine.choices, default=Engine.CHROMIUM
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
        """Return whether the browser should run headless in the current environment."""

        if self.mode == self.Mode.HEADLESS:
            return True
        if not os.environ.get("DISPLAY"):
            logger.warning("DISPLAY not set; forcing headless mode for %s", self)
            return True
        return False

    def create_driver(self) -> PlaywrightDriver:
        """Launch a Playwright browser and return a Selenium-like wrapper driver."""

        playwright = sync_playwright().start()

        launchers = {
            self.Engine.CHROMIUM: playwright.chromium,
            self.Engine.FIREFOX: playwright.firefox,
            self.Engine.WEBKIT: playwright.webkit,
        }
        launcher = launchers.get(self.engine)
        if launcher is None:
            playwright.stop()
            raise UnsupportedBrowserEngineError(
                f"Unsupported browser engine: {self.engine}"
            )

        launch_kwargs = {"headless": self._headless_mode()}
        if self.binary_path:
            launch_kwargs["executable_path"] = self.binary_path

        browser = None
        context = None
        try:
            browser = launcher.launch(**launch_kwargs)
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            page = context.new_page()
        except Exception:
            if context is not None:
                with contextlib.suppress(Exception):
                    context.close()
            if browser is not None:
                with contextlib.suppress(Exception):
                    browser.close()
            with contextlib.suppress(Exception):
                playwright.stop()
            raise

        return PlaywrightDriver(
            playwright=playwright,
            browser=browser,
            context=context,
            page=page,
        )


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
        except ImportError:
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
                exec_globals = {"browser": driver, "driver": driver, "script": self}
                compiled = compile(body, f"<SeleniumScript {self.name}>", "exec")
                exec(compiled, exec_globals, exec_globals)
        finally:
            driver.quit()


class SessionCookie(Ownable):
    """Persistent browser session cookies for automation flows.

    The model stores cookie payloads in a Playwright-compatible shape so
    long-running jobs can reuse successful sessions across calls and restarts.
    """

    class State(models.TextChoices):
        ACTIVE = "active", _("Active")
        STALE = "stale", _("Stale")
        REJECTED = "rejected", _("Rejected")

    name = models.CharField(max_length=100, unique=True)
    source = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Optional source label (service, node, or account)."),
    )
    cookies = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Cookie list compatible with Playwright BrowserContext.add_cookies."),
    )
    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.ACTIVE,
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_validated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    rejection_count = models.PositiveIntegerField(default=0)
    last_rejection_reason = models.TextField(blank=True)

    objects = EntityManager()

    class Meta:
        verbose_name = _("Session Cookie")
        verbose_name_plural = _("Session Cookies")
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(user__isnull=True) & Q(group__isnull=True))
                    | (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="selenium_sessioncookie_owner_exclusive",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def natural_key(self):  # pragma: no cover - fixture helper
        return (self.name,)

    def clean_cookie_payload(self, payload: list[dict]) -> list[dict]:
        """Validate and normalize a cookie list for persistence."""

        if not isinstance(payload, list):
            raise InvalidCookiePayloadError("Cookie payload must be a list of mappings.")
        for cookie in payload:
            if not isinstance(cookie, dict):
                raise InvalidCookiePayloadError(
                    "Each cookie entry must be a mapping with cookie attributes."
                )
            if "name" not in cookie or "value" not in cookie:
                raise InvalidCookiePayloadError(
                    "Each cookie must include both 'name' and 'value' keys."
                )
            if not isinstance(cookie["name"], str) or not cookie["name"].strip():
                raise InvalidCookiePayloadError(
                    "Cookie 'name' must be a non-empty string."
                )
            if not isinstance(cookie["value"], str):
                raise InvalidCookiePayloadError("Cookie 'value' must be a string.")
        return payload

    def _save_fields(self, fields: list[str]) -> None:
        """Persist only selected fields when possible, falling back for new rows."""

        if self.pk is None:
            self.save()
            return
        self.save(update_fields=fields)

    def set_cookies(self, payload: list[dict], *, save: bool = True) -> None:
        """Assign cookie payload and optionally persist the model."""

        self.cookies = self.clean_cookie_payload(payload)
        if save:
            self._save_fields(["cookies"])

    def mark_used(self, *, save: bool = True) -> None:
        """Mark the cookie as recently used by an automation task."""

        self.last_used_at = timezone.now()
        if save:
            self._save_fields(["last_used_at"])

    def mark_valid(self, *, save: bool = True) -> None:
        """Mark the session cookies as valid after successful use."""

        self.state = self.State.ACTIVE
        self.last_validated_at = timezone.now()
        self.last_rejection_reason = ""
        if save:
            self._save_fields(["state", "last_validated_at", "last_rejection_reason"])

    def mark_rejected(self, reason: str, *, save: bool = True) -> None:
        """Mark the cookie jar as rejected by an upstream service."""

        normalized_reason = reason.strip()
        self.state = self.State.REJECTED
        self.last_rejection_reason = normalized_reason

        if save and self.pk is not None:
            type(self).objects.filter(pk=self.pk).update(
                state=self.State.REJECTED,
                rejection_count=F("rejection_count") + 1,
                last_rejection_reason=normalized_reason,
            )
            self.refresh_from_db(
                fields=["state", "rejection_count", "last_rejection_reason"]
            )
            return

        self.rejection_count += 1
        if save:
            self._save_fields(["state", "rejection_count", "last_rejection_reason"])

    def is_expired(self) -> bool:
        """Return ``True`` when the session cookie expiry has passed."""

        return bool(self.expires_at and self.expires_at <= timezone.now())

    def as_playwright_cookies(self) -> list[dict]:
        """Return normalized cookie payload for Playwright contexts."""

        payload = self.clean_cookie_payload(self.cookies)
        return [normalize_playwright_cookie(cookie) for cookie in payload]
