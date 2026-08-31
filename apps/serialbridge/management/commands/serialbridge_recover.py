from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deprecated shim: use serialbridge recover."

    def add_arguments(self, parser):
        parser.add_argument("--interface", required=True)
        parser.add_argument("--peer", required=True)
        parser.add_argument("--action")
        parser.add_argument("--service")
        parser.add_argument("--log-path")
        parser.add_argument("--line-count", type=int)
        parser.add_argument("--restore-network")

    def handle(self, *args, **options):
        command_options = {
            "interface": options["interface"],
            "peer": options["peer"],
            "service": options.get("service"),
            "restore_network": options.get("restore_network"),
        }
        if options.get("action") is not None:
            command_options["operation"] = options["action"]
        if options.get("log_path") is not None:
            command_options["log_path"] = options["log_path"]
        if options.get("line_count") is not None:
            command_options["line_count"] = options["line_count"]

        call_command("serialbridge", "recover", **command_options)
