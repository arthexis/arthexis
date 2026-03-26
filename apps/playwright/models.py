from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager
from apps.core.models import Ownable
from apps.core.ui import has_graphical_display
from apps.features.utils import is_suite_feature_enabled
from apps.nodes.feature_detection import is_feature_active_for_node
from .playwright import normalize_playwright_cookie

if TYPE_CHECKING:
    from playwright.sync_api import Browser as PlaywrightBrowserInstance
    from playwright.sync_api import BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)
PLAYWRIGHT_AUTOMATION_FEATURE_SLUG = "playwright-automation"


def _load_sync_playwright():
    """Return Playwright sync launcher or raise a descriptive error."""

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "The 'playwright' package is required by playwright models. "
            "Install project optional dependency group 'ci' or add playwright."
        ) from exc
    return sync_playwright


class UnsupportedBrowserEngineError(ValueError):
    """Raised when a browser engine cannot be launched via Playwright."""


class InvalidCookiePayloadError(ValueError):
    """Raised when a session cookie payload does not match the expected shape."""


class PlaywrightRuntimeDisabledError(RuntimeError):
    """Raised when Playwright runtime automation is globally disabled."""


class PlaywrightEngineFeatureDisabledError(RuntimeError):
    """Raised when a Playwright browser engine is unavailable on the local node."""


def _ensure_playwright_runtime_enabled() -> None:
    """Raise when the global Playwright automation suite feature is disabled."""

    if not is_suite_feature_enabled(PLAYWRIGHT_AUTOMATION_FEATURE_SLUG, default=True):
        raise PlaywrightRuntimeDisabledError(
            "Playwright automation is disabled by suite feature "
            f"'{PLAYWRIGHT_AUTOMATION_FEATURE_SLUG}'."
        )


def _ensure_engine_feature_enabled(engine: str) -> None:
    """Raise when the local node does not expose the requested Playwright engine."""

    from apps.nodes.models import Node

    feature_map = {
        PlaywrightBrowser.Engine.CHROMIUM: "playwright-browser-chromium",
        PlaywrightBrowser.Engine.FIREFOX: "playwright-browser-firefox",
        PlaywrightBrowser.Engine.WEBKIT: "playwright-browser-webkit",
    }
    node_feature_slug = feature_map.get(engine)
    if node_feature_slug is None:
        raise UnsupportedBrowserEngineError(f"Unsupported browser engine: {engine}")
    local = Node.get_local()
    if local is None:
        return
    if not is_feature_active_for_node(node=local, slug=node_feature_slug):
        raise PlaywrightEngineFeatureDisabledError(
            f"Playwright engine '{engine}' is disabled on local node feature '{node_feature_slug}'."
        )


@dataclass
class PlaywrightDriver:
    """Compatibility wrapper around Playwright page/context/browser lifecycle."""

    playwright: "Playwright"
    browser: "PlaywrightBrowserInstance"
    context: "BrowserContext"
    page: "Page"

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

    def save_screenshot(self, path: str, *, full_page: bool = True) -> bool:
        """Capture a page screenshot to ``path`` and return ``True`` when saved."""

        self.page.screenshot(path=path, full_page=full_page)
        return True

    def quit(self) -> None:
        """Close Playwright resources in the correct order."""

        with contextlib.suppress(Exception):
            self.context.close()
        with contextlib.suppress(Exception):
            self.browser.close()
        with contextlib.suppress(Exception):
            self.playwright.stop()


class PlaywrightBrowserManager(EntityManager):
    def get_by_natural_key(self, name: str):  # pragma: no cover
        return self.get(name=name)

    def default(self) -> "PlaywrightBrowser | None":
        return self.filter(is_default=True).first()


class PlaywrightBrowser(Entity):
    """Browser launch profile for Playwright automation."""

    class Engine(models.TextChoices):
        CHROMIUM = "chromium", _("Chromium")
        FIREFOX = "firefox", _("Firefox")
        WEBKIT = "webkit", _("WebKit")

    class Mode(models.TextChoices):
        HEADED = "headed", _("Headed")
        HEADLESS = "headless", _("Headless")

    name = models.CharField(max_length=100, unique=True)
    engine = models.CharField(max_length=20, choices=Engine.choices, default=Engine.CHROMIUM)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.HEADED)
    binary_path = models.CharField(max_length=255, blank=True, help_text=_("Optional browser binary override."))
    is_default = models.BooleanField(default=False)

    objects = PlaywrightBrowserManager()

    class Meta:
        verbose_name = _("Playwright Browser")
        verbose_name_plural = _("Playwright Browsers")
        constraints = [
            models.UniqueConstraint(fields=["is_default"], condition=Q(is_default=True), name="playwright_browser_single_default")
        ]

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    @classmethod
    def default(cls) -> "PlaywrightBrowser | None":
        return cls.objects.default()

    def _headless_mode(self) -> bool:
        if self.mode == self.Mode.HEADLESS:
            return True
        if not has_graphical_display():
            logger.warning("No graphical display available; forcing headless mode for %s", self)
            return True
        return False

    def create_driver(self) -> PlaywrightDriver:
        """Launch this configured browser profile."""

        _ensure_playwright_runtime_enabled()
        _ensure_engine_feature_enabled(self.engine)

        sync_playwright = _load_sync_playwright()
        playwright = sync_playwright().start()
        launchers = {
            self.Engine.CHROMIUM: playwright.chromium,
            self.Engine.FIREFOX: playwright.firefox,
            self.Engine.WEBKIT: playwright.webkit,
        }
        launcher = launchers.get(self.engine)
        if launcher is None:
            playwright.stop()
            raise UnsupportedBrowserEngineError(f"Unsupported browser engine: {self.engine}")
        launch_kwargs = {"headless": self._headless_mode()}
        if self.binary_path.strip():
            launch_kwargs["executable_path"] = self.binary_path.strip()
        browser = None
        context = None
        try:
            browser = launcher.launch(**launch_kwargs)
            context = browser.new_context()
            page = context.new_page()
        except Exception:
            if context is not None:
                with contextlib.suppress(Exception):
                    context.close()
            if browser is not None:
                with contextlib.suppress(Exception):
                    browser.close()
            playwright.stop()
            raise
        return PlaywrightDriver(playwright=playwright, browser=browser, context=context, page=page)


class SessionCookie(Ownable):
    """Persistent browser session cookies for automation flows."""

    owner_required = True

    class State(models.TextChoices):
        ACTIVE = "active", _("Active")
        STALE = "stale", _("Stale")
        REJECTED = "rejected", _("Rejected")

    name = models.CharField(max_length=100, unique=True)
    source = models.CharField(max_length=100, blank=True)
    cookies = models.JSONField(default=list, blank=True)
    state = models.CharField(max_length=20, choices=State.choices, default=State.ACTIVE)
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
            models.CheckConstraint(condition=((Q(user__isnull=False) & Q(group__isnull=True)) | (Q(user__isnull=True) & Q(group__isnull=False))), name="playwright_sessioncookie_owner_exclusive")
        ]
        indexes = [models.Index(fields=["state", "expires_at"], name="pw_sc_state_exp_idx")]

    def clean_cookie_payload(self, payload: list[dict]) -> list[dict]:
        """Validate cookie payload structure and required ``name``/``value`` fields."""
        if not isinstance(payload, list):
            raise InvalidCookiePayloadError("Cookie payload must be a list of mappings.")
        for cookie in payload:
            if not isinstance(cookie, dict):
                raise InvalidCookiePayloadError("Each cookie entry must be a mapping with cookie attributes.")
            if "name" not in cookie or "value" not in cookie:
                raise InvalidCookiePayloadError("Each cookie must include both 'name' and 'value' keys.")

            name = cookie.get("name")
            value = cookie.get("value")
            if not isinstance(name, str) or not name.strip():
                raise InvalidCookiePayloadError("Cookie 'name' must be a non-empty string.")
            if not isinstance(value, str):
                raise InvalidCookiePayloadError("Cookie 'value' must be a string.")
        return payload

    def _save_fields(self, fields: list[str]) -> None:
        if self.pk is None:
            self.save()
            return
        self.save(update_fields=fields)

    def set_cookies(self, payload: list[dict], *, save: bool = True) -> None:
        self.cookies = self.clean_cookie_payload(payload)
        if save:
            self._save_fields(["cookies"])

    def mark_rejected(self, reason: str, *, save: bool = True) -> None:
        normalized_reason = reason.strip()
        self.state = self.State.REJECTED
        self.last_rejection_reason = normalized_reason
        if save and self.pk is not None:
            type(self).objects.filter(pk=self.pk).update(
                state=self.State.REJECTED,
                rejection_count=F("rejection_count") + 1,
                last_rejection_reason=normalized_reason,
            )
            self.refresh_from_db(fields=["state", "rejection_count", "last_rejection_reason"])
            return
        self.rejection_count += 1
        if save:
            self._save_fields(["state", "rejection_count", "last_rejection_reason"])

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

    def is_expired(self) -> bool:
        """Return ``True`` when the session cookie expiry has passed."""

        return bool(self.expires_at and self.expires_at <= timezone.now())

    def as_playwright_cookies(self) -> list[dict]:
        """Return normalized cookie payload for Playwright contexts."""

        payload = self.clean_cookie_payload(self.cookies)
        return [normalize_playwright_cookie(cookie) for cookie in payload]
