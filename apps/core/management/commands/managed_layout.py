from django.core.management.base import BaseCommand

from apps.core.system.lifecycle import managed_layout


class Command(BaseCommand):
    help = "Show the canonical GWAY-managed Arthexis filesystem layout"

    def add_arguments(self, parser):
        parser.add_argument("--root", default=None)

    def handle(self, *args, **options):
        layout = managed_layout(options["root"])
        self.stdout.write(f"root={layout.root}")
        self.stdout.write(f"checkout={layout.checkout}")
        self.stdout.write(f"environment={layout.environment}")
        self.stdout.write(f"python={layout.python}")
