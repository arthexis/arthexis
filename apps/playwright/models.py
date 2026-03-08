from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from typing import TYPE_CHECKING

from apps.content.models import ContentSample
from apps.nodes.feature_checks import get_screenshot_runtime_capability
from apps.content.utils import save_screenshot
from apps.core.entity import Entity, EntityManager
from apps.core.models import Ownable
from apps.sigils.sigil_resolver import resolve_sigils
from .playwright import normalize_playwright_cookie

if TYPE_CHECKING:
    from playwright.sync_api import Browser as PlaywrightBrowserInstance
    from playwright.sync_api import BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)
SCREENSHOT_DIR = settings.LOG_DIR / "screenshots"


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
        """Resolve browser mode using centralized screenshot runtime capability."""

        if self.mode == self.Mode.HEADLESS:
            return True
        capability = get_screenshot_runtime_capability()
        if not capability.display_available:
            logger.warning(
                "DISPLAY not set; forcing headless mode for %s (%s)",
                self,
                "; ".join(capability.diagnostics),
            )
            return True
        return False

    def create_driver(self) -> PlaywrightDriver:
        """Launch this configured browser profile."""

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


class PlaywrightScript(Entity):
    """Script that can drive a Playwright browser profile."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    start_url = models.URLField(blank=True)
    script = models.TextField(blank=True, help_text="Inline Python to execute with the browser available as `browser`.")
    python_path = models.CharField(max_length=255, blank=True, help_text="Dotted path to callable accepting the browser.")

    objects = EntityManager()

    class Meta:
        verbose_name = _("Playwright Script")
        verbose_name_plural = _("Playwright Scripts")

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def _resolved_body(self, *, current=None) -> str:
        return resolve_sigils(self.script or "", current=current)

    def _resolved_start_url_and_body(self, *, current=None) -> tuple[str, str]:
        start_url = resolve_sigils(self.start_url or "", current=current).strip()
        resolved_body = self._resolved_body(current=current)
        body = resolved_body.strip()
        for idx, raw_line in enumerate(resolved_body.splitlines()):
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith(("http://", "https://")):
                if not start_url:
                    start_url = stripped
                body = "\n".join(resolved_body.splitlines()[idx + 1 :]).strip()
            break
        return start_url, body

    def _load_callable(self, *, current=None):
        path = resolve_sigils(self.python_path or "", current=current).strip()
        if not path:
            return None
        return import_string(path)

    def execute(self, browser: PlaywrightBrowser | None = None, *, current=None):
        """Execute this script against the selected browser profile."""

        active_browser = browser or PlaywrightBrowser.default()
        if active_browser is None:
            raise PlaywrightBrowser.DoesNotExist("No default Playwright browser configured.")
        driver = active_browser.create_driver()
        try:
            start_url, body = self._resolved_start_url_and_body(current=current)
            if start_url:
                driver.get(start_url)
            callback = self._load_callable(current=current)
            if callback is not None:
                callback(driver, script=self)
                return
            if body:
                globals_map = {"browser": driver, "driver": driver, "script": self}
                compiled = compile(body, f"<PlaywrightScript {self.name}>", "exec")
                exec(compiled, globals_map, globals_map)
        finally:
            driver.quit()


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
        if not isinstance(payload, list):
            raise InvalidCookiePayloadError("Cookie payload must be a list of mappings.")
        for cookie in payload:
            if not isinstance(cookie, dict):
                raise InvalidCookiePayloadError("Each cookie entry must be a mapping with cookie attributes.")
            if "name" not in cookie or "value" not in cookie:
                raise InvalidCookiePayloadError("Each cookie must include both 'name' and 'value' keys.")
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


class WebsiteScreenshotSchedule(Entity):
    """Periodic Playwright website screenshot configuration."""

    slug = models.SlugField(max_length=100, unique=True)
    label = models.CharField(max_length=150)
    url = models.URLField()
    is_active = models.BooleanField(default=True)
    sampling_period_minutes = models.PositiveIntegerField(null=True, blank=True)
    last_sampled_at = models.DateTimeField(null=True, blank=True)
    favored_engine = models.CharField(max_length=20, choices=PlaywrightBrowser.Engine.choices, default=PlaywrightBrowser.Engine.CHROMIUM)
    fallback_engines = models.JSONField(default=list, blank=True, help_text="Optional engine list to try after favored engine.")
    pre_commands = models.JSONField(default=list, blank=True, help_text="List of Playwright page commands.")
    post_navigation_delay_ms = models.PositiveIntegerField(default=0)
    timeout_ms = models.PositiveIntegerField(default=30000)
    viewport_width = models.PositiveIntegerField(default=1280)
    viewport_height = models.PositiveIntegerField(default=720)
    full_page = models.BooleanField(default=True)

    class Meta:
        ordering = ["label", "slug"]
        verbose_name = "Website Screenshot Schedule"
        verbose_name_plural = "Website Screenshot Schedules"

    def browser_engine_candidates(self) -> list[str]:
        """Return deduplicated browser engine candidates in attempt order."""

        engines = [self.favored_engine]
        engines.extend(engine for engine in self.fallback_engines if isinstance(engine, str))
        deduped: list[str] = []
        for engine in engines:
            if engine not in deduped and engine in PlaywrightBrowser.Engine.values:
                deduped.append(engine)

        from apps.nodes.models import Node

        local = Node.get_local()
        feature_map = {
            PlaywrightBrowser.Engine.CHROMIUM: "playwright-browser-chromium",
            PlaywrightBrowser.Engine.FIREFOX: "playwright-browser-firefox",
            PlaywrightBrowser.Engine.WEBKIT: "playwright-browser-webkit",
        }
        if local is None:
            return deduped or [PlaywrightBrowser.Engine.CHROMIUM]

        available = [
            engine
            for engine in deduped
            if local.has_feature(feature_map[engine])
        ]
        return available or deduped or [PlaywrightBrowser.Engine.CHROMIUM]


class WebsiteScreenshotRun(Entity):
    """Execution log for website screenshot schedules."""

    schedule = models.ForeignKey(WebsiteScreenshotSchedule, on_delete=models.CASCADE, related_name="runs")
    document = models.JSONField(default=dict)
    content_sample = models.ForeignKey(ContentSample, on_delete=models.SET_NULL, null=True, blank=True, related_name="website_screenshot_runs")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


def execute_website_screenshot_schedule(schedule: WebsiteScreenshotSchedule, *, user=None) -> WebsiteScreenshotRun:
    """Execute one screenshot schedule and persist a content sample."""

    from playwright.sync_api import Error as PlaywrightError

    sync_playwright = _load_sync_playwright()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    filename = SCREENSHOT_DIR / f"{schedule.slug}-{datetime.now(dt_timezone.utc):%Y%m%d%H%M%S}.png"

    errors: dict[str, str] = {}
    for engine in schedule.browser_engine_candidates():
        try:
            with sync_playwright() as playwright:
                launcher = getattr(playwright, engine)
                browser = launcher.launch(headless=True)
                context = browser.new_context(viewport={"width": schedule.viewport_width, "height": schedule.viewport_height})
                page = context.new_page()
                page.goto(schedule.url, timeout=schedule.timeout_ms, wait_until="networkidle")
                _run_pre_commands(page, schedule.pre_commands, default_timeout_ms=schedule.timeout_ms)
                if schedule.post_navigation_delay_ms:
                    page.wait_for_timeout(schedule.post_navigation_delay_ms)
                page.screenshot(path=str(filename), full_page=schedule.full_page)
                context.close()
                browser.close()
            sample = save_screenshot(filename, method="PLAYWRIGHT_SCHEDULE", user=user, link_duplicates=True)
            schedule.last_sampled_at = timezone.now()
            schedule.save(update_fields=["last_sampled_at"])
            return WebsiteScreenshotRun.objects.create(
                schedule=schedule,
                document={"engine": engine, "url": schedule.url, "path": filename.as_posix()},
                content_sample=sample,
            )
        except (PlaywrightError, KeyError, ValueError) as exc:
            errors[engine] = str(exc)
            continue

    raise RuntimeError(f"All browser engines failed for schedule {schedule.slug}: {errors}")


def _run_pre_commands(page, pre_commands, *, default_timeout_ms: int) -> None:
    """Execute supported pre-navigation commands against a Playwright page."""

    for command in pre_commands:
        if not isinstance(command, dict):
            continue
        action = command.get("action")
        selector = command.get("selector")
        if action in {"click", "fill", "wait_for_selector"} and not selector:
            raise ValueError(f"Missing selector for pre-command action: {action}")
        if action == "click":
            page.locator(selector).click()
        elif action == "fill":
            page.locator(selector).fill(command.get("value", ""))
        elif action == "wait_for_selector":
            page.wait_for_selector(selector, timeout=command.get("timeout_ms", default_timeout_ms))
        elif action == "wait_for_timeout":
            page.wait_for_timeout(int(command.get("ms", 250)))


def schedule_pending_website_screenshots(now=None) -> list[int]:
    """Execute screenshot schedules that are due."""

    now = now or timezone.now()
    executed: list[int] = []
    for schedule in WebsiteScreenshotSchedule.objects.filter(is_active=True).exclude(sampling_period_minutes__isnull=True):
        if schedule.sampling_period_minutes is None:
            continue
        due_at = schedule.last_sampled_at or (now - timedelta(minutes=schedule.sampling_period_minutes + 1))
        if now < due_at + timedelta(minutes=schedule.sampling_period_minutes):
            continue
        try:
            execute_website_screenshot_schedule(schedule)
            executed.append(schedule.pk)
        except Exception:
            logger.exception("Failed to execute screenshot schedule %s", schedule.pk)
    return executed
