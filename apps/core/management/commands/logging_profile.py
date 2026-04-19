from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from utils.loggers.config import resolve_log_formatter
from utils.loggers.paths import select_log_dir

UNSET_VALUE = "(unset)"


class Command(BaseCommand):
    help = "Show the active Arthexis logging profile and observability integration wiring."

    def handle(self, *args, **options):
        """Print active logging formatter, log directory, and observability wiring."""

        formatter_mode = resolve_log_formatter()
        log_dir = getattr(settings, "LOG_DIR", None)
        configured_log_dir = Path(log_dir) if log_dir else select_log_dir(Path(settings.BASE_DIR))
        grafana_url = (getattr(settings, "ARTHEXIS_GRAFANA_URL", "") or "").strip() or UNSET_VALUE
        loki_url = (getattr(settings, "ARTHEXIS_LOKI_URL", "") or "").strip() or UNSET_VALUE
        promtail_config = (getattr(settings, "ARTHEXIS_PROMTAIL_CONFIG", "") or "").strip() or UNSET_VALUE

        self.stdout.write("Logging profile")
        self.stdout.write(f"- formatter: {formatter_mode}")
        self.stdout.write(f"- log_dir: {configured_log_dir}")
        self.stdout.write("Observability wiring")
        self.stdout.write(f"- ARTHEXIS_GRAFANA_URL: {grafana_url}")
        self.stdout.write(f"- ARTHEXIS_LOKI_URL: {loki_url}")
        self.stdout.write(f"- ARTHEXIS_PROMTAIL_CONFIG: {promtail_config}")
