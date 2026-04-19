from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from utils.loggers.config import resolve_log_formatter
from utils.loggers.paths import select_log_dir


class Command(BaseCommand):
    help = "Show the active Arthexis logging profile and observability integration wiring."

    def handle(self, *args, **options):
        formatter_mode = resolve_log_formatter()
        configured_log_dir = Path(getattr(settings, "LOG_DIR", select_log_dir(Path(settings.BASE_DIR))))
        grafana_url = os.environ.get("ARTHEXIS_GRAFANA_URL", "").strip() or "(unset)"
        loki_url = os.environ.get("ARTHEXIS_LOKI_URL", "").strip() or "(unset)"
        promtail_config = os.environ.get("ARTHEXIS_PROMTAIL_CONFIG", "").strip() or "(unset)"

        self.stdout.write("Logging profile")
        self.stdout.write(f"- formatter: {formatter_mode}")
        self.stdout.write(f"- log_dir: {configured_log_dir}")
        self.stdout.write("Observability wiring")
        self.stdout.write(f"- ARTHEXIS_GRAFANA_URL: {grafana_url}")
        self.stdout.write(f"- ARTHEXIS_LOKI_URL: {loki_url}")
        self.stdout.write(f"- ARTHEXIS_PROMTAIL_CONFIG: {promtail_config}")
