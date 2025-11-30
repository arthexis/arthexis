"""Utilities for declarative screenshot specifications."""

from __future__ import annotations

import base64
import logging
import shutil
from dataclasses import dataclass, field
from datetime import timedelta
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence
from urllib.parse import urljoin, urlparse

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.nodes.models import ContentSample
from apps.nodes.utils import capture_screenshot, save_screenshot

logger = logging.getLogger(__name__)

SPEC_METHOD_PREFIX = "spec:"


class ScreenshotUnavailable(RuntimeError):
    """Raised when a screenshot cannot be automated for a spec."""


@dataclass(slots=True)
class ScreenshotSpec:
    """Declarative description of a UI screenshot scenario."""

    slug: str
    url: str
    coverage_globs: Sequence[str] = field(default_factory=list)
    setup: Optional[Callable[["ScreenshotContext"], None]] = None
    manual_reason: str | None = None

    def matches(self, changed_files: Iterable[str]) -> bool:
        """Return ``True`` when the spec should run for ``changed_files``."""

        patterns = list(self.coverage_globs)
        if not patterns:
            return True
        for name in changed_files:
            for pattern in patterns:
                if fnmatch(name, pattern) or Path(name).match(pattern):
                    return True
        return False


@dataclass(slots=True)
class ScreenshotResult:
    """Outcome of a screenshot capture."""

    spec: ScreenshotSpec
    image_path: Path
    base64_path: Path
    sample: ContentSample | None


class ScreenshotContext:
    """Mutable runtime helpers exposed to spec setup callables."""

    def __init__(self, live_server_url: str):
        self.live_server_url = live_server_url.rstrip("/")
        self.client = Client()
        self.cookies: list[dict[str, object]] = []

    def build_url(self, path: str) -> str:
        return urljoin(f"{self.live_server_url}/", path)

    def load_fixtures(self, *fixtures: str) -> None:
        for fixture in fixtures:
            call_command("loaddata", fixture)

    def add_client_cookies(self, client: Client | None = None) -> None:
        jar = (client or self.client).cookies
        domain = urlparse(self.live_server_url).hostname or "localhost"
        for cookie in jar:  # http.cookiejar.Cookie
            payload = {
                "name": cookie.name,
                "value": cookie.value,
                "path": cookie.path or "/",
                "domain": domain,
            }
            if cookie.secure:
                payload["secure"] = True
            self.cookies.append(payload)


class SpecRegistry:
    """In-memory collection of screenshot specs."""

    def __init__(self) -> None:
        self._specs: dict[str, ScreenshotSpec] = {}

    def register(self, spec: ScreenshotSpec) -> ScreenshotSpec:
        if spec.slug in self._specs:
            raise ValueError(f"Screenshot spec '{spec.slug}' already registered")
        self._specs[spec.slug] = spec
        return spec

    def unregister(self, slug: str) -> None:
        self._specs.pop(slug, None)

    def get(self, slug: str) -> ScreenshotSpec:
        try:
            return self._specs[slug]
        except KeyError as exc:  # pragma: no cover - defensive
            raise KeyError(f"Unknown screenshot spec '{slug}'") from exc

    def all(self) -> list[ScreenshotSpec]:
        return list(self._specs.values())


registry = SpecRegistry()


class _LiveServerHarness(StaticLiveServerTestCase):
    """Private harness to run specs without pytest integration."""

    @classmethod
    def setUpClass(cls):  # pragma: no cover - exercised by runner
        super().setUpClass()


class ScreenshotSpecRunner:
    """Context manager that executes screenshot specs."""

    def __init__(
        self, output_dir: Path | str, *, retention: timedelta | None = timedelta(days=7)
    ) -> None:
        self.output_dir = Path(output_dir)
        self.retention = retention
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "ScreenshotSpecRunner":
        _LiveServerHarness.setUpClass()
        self._live_server_url = _LiveServerHarness.live_server_url
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _LiveServerHarness.tearDownClass()

    def run(self, spec: ScreenshotSpec) -> ScreenshotResult:
        if spec.manual_reason:
            raise ScreenshotUnavailable(spec.manual_reason)

        context = ScreenshotContext(self._live_server_url)
        if spec.setup:
            spec.setup(context)
        context.add_client_cookies()
        url = context.build_url(spec.url)
        screenshot_path = capture_screenshot(url, cookies=context.cookies or None)
        sample = save_screenshot(
            screenshot_path, method=f"{SPEC_METHOD_PREFIX}{spec.slug}"
        )
        self._cleanup_content_samples()
        image_path = self.output_dir / f"{spec.slug}.png"
        shutil.copyfile(screenshot_path, image_path)
        base64_path = self.output_dir / f"{spec.slug}.base64"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        base64_path.write_text(encoded, encoding="utf-8")
        return ScreenshotResult(
            spec=spec, image_path=image_path, base64_path=base64_path, sample=sample
        )

    def _cleanup_content_samples(self) -> None:
        if not self.retention:
            return
        threshold = timezone.now() - self.retention
        stale = ContentSample.objects.filter(
            created_at__lt=threshold, method__startswith=SPEC_METHOD_PREFIX
        )
        if stale.exists():
            deleted, _ = stale.delete()
            logger.info("Deleted %s stale screenshot content samples", deleted)
