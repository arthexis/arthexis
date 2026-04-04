"""Optional command exposed by the reference plugin."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Emit a simple plugin health message"

    def handle(self, *args, **options):
        del args, options
        self.stdout.write("arthexis-plugin-sample: ok")
