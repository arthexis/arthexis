from __future__ import annotations

import contextlib
import logging
import os

from django.db import models
from django.db.models import Q
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions

from apps.core.entity import Entity, EntityManager
from apps.selenium.utils.firefox import ensure_geckodriver, find_firefox_binary
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

    def _build_firefox_options(self) -> FirefoxOptions:
        options = FirefoxOptions()
        binary = find_firefox_binary(self.binary_path)
        if binary:
            options.binary_location = binary
        mode = self.mode
        if mode == self.Mode.HEADED and not os.environ.get("DISPLAY"):
            logger.warning("DISPLAY not set; forcing headless mode for %s", self)
            mode = self.Mode.HEADLESS
        if mode == self.Mode.HEADLESS:
            options.add_argument("-headless")
        return options

    def create_driver(self):
        if self.engine != self.Engine.FIREFOX:
            raise RuntimeError(f"Unsupported browser engine: {self.engine}")

        ensure_geckodriver()
        options = self._build_firefox_options()
        return webdriver.Firefox(options=options)


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
                exec_globals = {"browser": driver, "driver": driver, "script": self}
                compiled = compile(body, f"<SeleniumScript {self.name}>", "exec")
                exec(compiled, exec_globals, exec_globals)
        finally:
            with contextlib.suppress(Exception):
                driver.quit()
