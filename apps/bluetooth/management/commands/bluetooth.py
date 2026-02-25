from django.core.management.base import BaseCommand, CommandError

from apps.bluetooth.models import BluetoothDevice
from apps.bluetooth.services import (
    BluetoothCommandError,
    discover_and_sync_devices,
    register_device,
    set_adapter_power,
    unregister_device,
)


class Command(BaseCommand):
    """Manage Bluetooth adapter power and known devices."""

    help = "Control Bluetooth power, discovery, and device registration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--enable", action="store_true", help="Enable Bluetooth adapter power."
        )
        parser.add_argument(
            "--disable", action="store_true", help="Disable Bluetooth adapter power."
        )
        parser.add_argument(
            "--discover",
            action="store_true",
            help="Run discovery and sync known devices.",
        )
        parser.add_argument(
            "--timeout", type=int, default=4, help="Discovery timeout in seconds."
        )
        parser.add_argument(
            "--register",
            metavar="ADDRESS",
            help="Mark a device as registered by address.",
        )
        parser.add_argument(
            "--unregister",
            metavar="ADDRESS",
            help="Mark a device as unregistered by address.",
        )
        parser.add_argument(
            "--list", action="store_true", help="List known devices from the database."
        )

    def handle(self, *args, **options):
        try:
            if options["enable"] and options["disable"]:
                raise CommandError("Use either --enable or --disable, not both.")

            if options["enable"]:
                state = set_adapter_power(True)
                self.stdout.write(self.style.SUCCESS(f"Bluetooth enabled: {state}"))
            elif options["disable"]:
                state = set_adapter_power(False)
                self.stdout.write(self.style.SUCCESS(f"Bluetooth disabled: {state}"))

            if options["discover"]:
                result = discover_and_sync_devices(timeout_s=max(options["timeout"], 0))
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Discovery done. count={result['count']} created={result['created']} updated={result['updated']}"
                    )
                )

            if options["register"]:
                device = register_device(options["register"])
                self.stdout.write(
                    self.style.SUCCESS(f"Registered device {device.address}")
                )

            if options["unregister"]:
                device = unregister_device(options["unregister"])
                self.stdout.write(
                    self.style.SUCCESS(f"Unregistered device {device.address}")
                )

            if options["list"]:
                for device in BluetoothDevice.objects.order_by("address"):
                    self.stdout.write(
                        f"{device.address} name={device.name or '-'} registered={device.is_registered} paired={device.paired} connected={device.connected}"
                    )
        except BluetoothDevice.DoesNotExist as exc:
            raise CommandError(str(exc)) from exc
        except BluetoothCommandError as exc:
            raise CommandError(str(exc)) from exc
