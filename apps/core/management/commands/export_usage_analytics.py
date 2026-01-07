import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.core.analytics import build_usage_summary
from apps.core.models import UsageEvent


class Command(BaseCommand):
    help = "Export usage analytics summaries to JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Number of days of history to include (default: 30)",
        )
        parser.add_argument(
            "--output",
            type=str,
            help="Optional file path for writing the summary JSON.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        summary = build_usage_summary(days=days, queryset=UsageEvent.objects.all())
        payload = json.dumps(summary, default=str, indent=2)

        output_path = options.get("output")
        if output_path:
            path = Path(output_path)
            path.write_text(payload + "\n", encoding="utf-8")
            self.stdout.write(
                self.style.SUCCESS(f"Usage analytics summary written to {path}")
            )
        else:
            self.stdout.write(payload)
