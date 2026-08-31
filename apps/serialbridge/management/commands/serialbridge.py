import subprocess
from collections import deque
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.serialbridge.models import (
    SerialCommandAudit,
    SerialInterface,
    SerialPeer,
    SerialSession,
)

ALLOWED_SERVICES = {
    "arthexis",
    "celery-arthexis",
    "celery-beat-arthexis",
}
ALLOWED_NET_IFACES = {"eth0", "wlan0", "wlan1"}


def get_log_dir():
    return Path(settings.LOG_DIR)


def get_default_log_path():
    return get_log_dir() / settings.LOG_FILE_NAME


class Command(BaseCommand):
    help = "Serial bridge operations with recover and ping subcommands."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action", required=True)

        ping_parser = subparsers.add_parser("ping")
        ping_parser.add_argument("--interface", required=True)
        ping_parser.add_argument("--peer", required=True)

        recover_parser = subparsers.add_parser("recover")
        recover_parser.add_argument("--interface", required=True)
        recover_parser.add_argument("--peer", required=True)
        recover_parser.add_argument(
            "--operation",
            choices=(
                "diagnostics_manifest",
                "tail_logs",
                "restart_service",
                "safe_mode",
            ),
        )
        recover_parser.add_argument("--service")
        recover_parser.add_argument("--log-path", default=str(get_default_log_path()))
        recover_parser.add_argument("--line-count", type=int, default=80)
        recover_parser.add_argument("--restore-network")

    def handle(self, *args, **options):
        interface = self._get_interface(options["interface"])
        peer = self._get_peer(interface, options["peer"])
        action = options["action"]
        if action == "ping":
            self._handle_ping(interface, peer)
            return

        self._handle_recover(interface, peer, options)

    def _handle_ping(self, interface, peer):
        session, _ = SerialSession.objects.get_or_create(interface=interface, peer=peer)
        session.mark_ping()
        SerialPeer.objects.filter(pk=peer.pk).update(
            last_seen_at=session.last_seen_at,
            updated_at=session.last_seen_at,
        )
        SerialCommandAudit.objects.create(
            interface=interface,
            peer=peer,
            command=SerialCommandAudit.CommandType.PING,
            payload={"action": "ping", "source": "management-command"},
            result=SerialCommandAudit.Result.SUCCESS,
            result_message="Ping acknowledged.",
        )
        self.stdout.write(
            self.style.SUCCESS(f"Ping recorded for {interface.name} -> {peer.node_id}")
        )

    def _handle_recover(self, interface, peer, options):
        operation = options.get("operation")
        restore_network = options.get("restore_network")
        if bool(operation) == bool(restore_network):
            raise CommandError(
                "Provide exactly one of --operation or --restore-network."
            )

        if restore_network:
            payload, msg, cmd = self._restore_network(restore_network)
            operation = "restore_network"
        elif operation == "diagnostics_manifest":
            payload, msg, cmd = self._diagnostics_manifest(interface, peer)
        elif operation == "tail_logs":
            payload, msg, cmd = self._tail_logs(
                options["log_path"], options["line_count"]
            )
        elif operation == "restart_service":
            payload, msg, cmd = self._restart_service(options.get("service"))
        elif operation == "safe_mode":
            payload, msg, cmd = self._safe_mode()
        else:
            raise CommandError(f"Unsupported recover operation: {operation}")

        SerialCommandAudit.objects.create(
            interface=interface,
            peer=peer,
            command=cmd,
            payload={"action": operation, **payload},
            result=SerialCommandAudit.Result.SUCCESS,
            result_message=msg,
        )
        self.stdout.write(self.style.SUCCESS(msg))

    def _get_interface(self, interface_name):
        try:
            return SerialInterface.objects.get(name=interface_name, is_enabled=True)
        except SerialInterface.DoesNotExist as exc:
            raise CommandError(
                f"Enabled interface '{interface_name}' does not exist."
            ) from exc

    def _get_peer(self, interface, peer_node):
        try:
            return SerialPeer.objects.get(
                interface=interface, node_id=peer_node, is_active=True
            )
        except SerialPeer.DoesNotExist as exc:
            raise CommandError(
                f"Active peer '{peer_node}' does not exist on interface '{interface.name}'."
            ) from exc

    def _diagnostics_manifest(self, interface, peer):
        return (
            {
                "interface": interface.name,
                "peer": peer.node_id,
                "command_log": str(get_default_log_path()),
                "available_operations": [
                    "diagnostics_manifest",
                    "tail_logs",
                    "restart_service",
                    "safe_mode",
                    "restore_network",
                ],
            },
            "Diagnostics manifest ready.",
            SerialCommandAudit.CommandType.DIAGNOSTICS,
        )

    def _tail_logs(self, log_path, line_count):
        bounded_count = min(max(line_count, 1), 200)
        path = Path(log_path).resolve()
        allowed_dir = get_log_dir().resolve()
        if not path.is_relative_to(allowed_dir):
            raise CommandError(f"Access to log path '{log_path}' is not allowed.")
        if not path.exists():
            raise CommandError(f"Log path does not exist: {log_path}")
        with path.open(encoding="utf-8", errors="replace") as handle:
            lines = [
                line.rstrip("\r\n") for line in deque(handle, maxlen=bounded_count)
            ]

        return (
            {"line_count": bounded_count, "log_path": str(path), "lines": lines},
            f"Returned {len(lines)} log lines.",
            SerialCommandAudit.CommandType.LOG_TAIL,
        )

    def _restart_service(self, service):
        if not service:
            raise CommandError("--service is required for restart_service")
        if service not in ALLOWED_SERVICES:
            raise CommandError(f"Service '{service}' is not in the recovery allowlist.")
        try:
            subprocess.run(
                ["sudo", "systemctl", "restart", service], check=True, timeout=20
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandError(f"Timed out restarting service '{service}'.") from exc
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise CommandError(f"Failed to restart service '{service}'.") from exc
        return (
            {"service": service},
            f"Restart request issued for {service}.",
            SerialCommandAudit.CommandType.RESTART,
        )

    def _restore_network(self, network_name):
        if network_name not in ALLOWED_NET_IFACES:
            raise CommandError("--restore-network must be one of eth0, wlan0, wlan1")
        try:
            subprocess.run(
                ["nmcli", "device", "set", network_name, "managed", "yes"],
                check=True,
                timeout=20,
            )
            subprocess.run(
                ["nmcli", "device", "disconnect", network_name], check=False, timeout=20
            )
            subprocess.run(
                ["nmcli", "device", "connect", network_name], check=True, timeout=20
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandError(
                f"Timed out restoring network interface '{network_name}'."
            ) from exc
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise CommandError(
                f"Failed to restore network interface '{network_name}'."
            ) from exc
        return (
            {"network": network_name},
            f"Network interface {network_name} restore/re-config requested.",
            SerialCommandAudit.CommandType.RESTORE_NETWORK,
        )

    def _safe_mode(self):
        return (
            {"safe_mode": "queued"},
            "Safe mode request accepted.",
            SerialCommandAudit.CommandType.SAFE_MODE,
        )
