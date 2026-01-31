from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.app.code_updates import get_application_code_cache_path


def _git_last_updated_on(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cI", "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=settings.BASE_DIR,
    )
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        updated = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return updated.date().isoformat()


def _fallback_last_updated_on(path: Path) -> str | None:
    latest_mtime: float | None = None
    try:
        if path.is_file():
            mtime = path.stat().st_mtime
            return datetime.fromtimestamp(mtime, tz=timezone.utc).date().isoformat()

        for entry in path.rglob("*"):
            if not entry.is_file():
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if latest_mtime is None or mtime > latest_mtime:
                latest_mtime = mtime
    except OSError:
        return None
    if latest_mtime is None:
        return None
    return datetime.fromtimestamp(latest_mtime, tz=timezone.utc).date().isoformat()


class Command(BaseCommand):
    help = "Build a static cache of last updated dates for installed apps."

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        apps_root = base_dir / "apps"
        payload: dict[str, str] = {}

        for config in django_apps.get_app_configs():
            app_path = Path(config.path)
            try:
                resolved = app_path.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(apps_root):
                continue
            updated_on = _git_last_updated_on(resolved) or _fallback_last_updated_on(
                resolved
            )
            if updated_on:
                payload[config.label] = updated_on

        cache_path = get_application_code_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        output = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "applications": dict(sorted(payload.items())),
        }
        cache_path.write_text(
            json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.stdout.write(f"Wrote {len(payload)} app entries to {cache_path}")
