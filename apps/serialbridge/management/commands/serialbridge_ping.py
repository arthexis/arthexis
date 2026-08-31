from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deprecated shim: use serialbridge ping."

    def add_arguments(self, parser):
        parser.add_argument("--interface", required=True)
        parser.add_argument("--peer", required=True)

    def handle(self, *args, **options):
        call_command("serialbridge", "ping", interface=options["interface"], peer=options["peer"])
