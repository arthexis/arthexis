from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Build the project README from stored sections."

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        output = base_dir / "README.md"

        ReadmeSection = apps.get_model("release", "ReadmeSection")
        pieces = list(
            ReadmeSection.objects.order_by("order").values_list("content", flat=True)
        )

        output.write_text("\n\n".join(pieces), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {output}"))
