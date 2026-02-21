"""Canonical RFID management command with action-based subcommands."""

from django.core.management.base import BaseCommand, CommandError

from apps.cards.management.commands._rfid_check_impl import (
    add_check_arguments,
    run_check_command,
)
from apps.cards.management.commands._rfid_doctor_impl import Command as DoctorImplCommand
from apps.cards.management.commands._rfid_export_impl import Command as ExportImplCommand
from apps.cards.management.commands._rfid_import_impl import Command as ImportImplCommand
from apps.cards.management.commands._rfid_service_impl import Command as ServiceImplCommand
from apps.cards.management.commands._rfid_watch_impl import Command as WatchImplCommand


class Command(BaseCommand):
    """Run RFID workflows via ``rfid <action>`` subcommands."""

    help = "Run RFID workflows via named subcommands."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action")

        check_parser = subparsers.add_parser("check", help="Validate an RFID tag or run a scan")
        add_check_arguments(check_parser)

        watch_parser = subparsers.add_parser("watch", help="Toggle always-on RFID watcher")
        watch_parser.add_argument("--stop", action="store_true", help="Stop the always-on watcher")

        service_parser = subparsers.add_parser("service", help="Run the RFID scanner UDP service")
        ServiceImplCommand().add_arguments(service_parser)

        doctor_parser = subparsers.add_parser("doctor", help="Run RFID diagnostics")
        DoctorImplCommand().add_arguments(doctor_parser)

        import_parser = subparsers.add_parser("import", help="Import RFIDs from CSV")
        ImportImplCommand().add_arguments(import_parser)

        export_parser = subparsers.add_parser("export", help="Export RFIDs to CSV")
        ExportImplCommand().add_arguments(export_parser)

    def handle(self, *args, **options):
        action = options.get("action")
        if action == "check":
            run_check_command(self, options)
            return

        impl_map = {
            "watch": WatchImplCommand,
            "service": ServiceImplCommand,
            "doctor": DoctorImplCommand,
            "import": ImportImplCommand,
            "export": ExportImplCommand,
        }
        impl_cls = impl_map.get(action)
        if impl_cls:
            self._run_impl(impl_cls, options)
            return

        raise CommandError("Missing or unknown action. Use one of: check, watch, service, doctor, import, export.")

    def _run_impl(self, impl_cls, options):
        impl = impl_cls()
        impl.stdout = self.stdout
        impl.stderr = self.stderr
        impl.handle(**{k: v for k, v in options.items() if k != "action"})
