from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Build the project README from the base file and app READMEs."

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        base_readme = base_dir / "README.base.md"
        output = base_dir / "README.md"

        pieces = []
        if base_readme.exists():
            pieces.append(base_readme.read_text(encoding="utf-8"))

        for app_config in apps.get_app_configs():
            path = Path(app_config.path)
            if path.parent == base_dir:
                app_readme = path / "README.md"
                if app_readme.exists():
                    pieces.append(app_readme.read_text(encoding="utf-8"))

        output.write_text("\n\n".join(pieces), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {output}"))
