from __future__ import annotations

import argparse
import getpass
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

SCAN_FIELDS = "IN-USE,SSID,SECURITY,SIGNAL"
AUTOCONNECT_PRIORITY = "100"

NMCLIRunner = Callable[[Sequence[str]], str]
InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]
PasswordFunc = Callable[[str], str]


class WifiLoginError(RuntimeError):
    """Raised when interactive Wi-Fi login cannot complete."""


@dataclass(frozen=True, slots=True)
class WifiNetwork:
    ssid: str
    security: str
    signal: int | None
    in_use: bool = False

    @property
    def display_security(self) -> str:
        return self.security or "open"


def _run_nmcli(args: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            ["nmcli", *args], capture_output=True, text=True, check=True
        )
    except FileNotFoundError as exc:
        raise WifiLoginError("nmcli is not available on this system.") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or str(exc)).strip()
        raise WifiLoginError(details) from exc
    return result.stdout


def _parse_network(fields: dict[str, str]) -> WifiNetwork | None:
    ssid = fields.get("SSID", "").strip()
    if not ssid:
        return None

    signal_value = fields.get("SIGNAL", "").strip()
    try:
        signal = int(signal_value)
    except ValueError:
        signal = None

    security = fields.get("SECURITY", "").strip()
    if security == "--":
        security = ""

    return WifiNetwork(
        ssid=ssid,
        security=security,
        signal=signal,
        in_use=fields.get("IN-USE", "").strip() == "*",
    )


def parse_wifi_scan_output(output: str) -> list[WifiNetwork]:
    """Parse multiline `nmcli device wifi list` output into unique SSIDs."""

    fields: dict[str, str] = {}
    best_by_network: dict[tuple[str, str], WifiNetwork] = {}

    def flush() -> None:
        network = _parse_network(fields)
        fields.clear()
        if network is None:
            return

        key = (network.ssid, network.security)
        previous = best_by_network.get(key)
        if previous is None:
            best_by_network[key] = network
            return

        previous_signal = previous.signal if previous.signal is not None else -1
        network_signal = network.signal if network.signal is not None else -1

        if network.in_use and not previous.in_use:
            best_by_network[key] = network
            return

        if network.in_use == previous.in_use and network_signal > previous_signal:
            best_by_network[key] = network

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip().upper()] = value.strip()

    flush()
    return sorted(
        best_by_network.values(),
        key=lambda network: (
            not network.in_use,
            -(network.signal if network.signal is not None else -1),
            network.ssid.lower(),
            network.security.lower(),
        ),
    )


def scan_wifi_networks(interface: str, *, runner: NMCLIRunner = _run_nmcli) -> list[WifiNetwork]:
    output = runner(
        [
            "-m",
            "multiline",
            "-f",
            SCAN_FIELDS,
            "device",
            "wifi",
            "list",
            "ifname",
            interface,
            "--rescan",
            "auto",
        ]
    )
    return parse_wifi_scan_output(output)


def security_requires_password(security: str) -> bool:
    normalized = security.strip().upper()
    return bool(normalized and normalized != "--")


def security_is_enterprise(security: str) -> bool:
    normalized = security.strip().upper()
    return "802.1X" in normalized or "EAP" in normalized


def _resolve_active_connection(interface: str, *, runner: NMCLIRunner = _run_nmcli) -> str:
    connection_name = runner(
        ["-g", "GENERAL.CONNECTION", "device", "show", interface]
    ).strip()
    if ":" in connection_name and connection_name.startswith("GENERAL.CONNECTION"):
        connection_name = connection_name.split(":", 1)[1].strip()
    if not connection_name or connection_name == "--":
        raise WifiLoginError(
            f"Connected on {interface}, but could not resolve the active connection name."
        )
    return connection_name


def connect_wifi_network(
    *,
    ssid: str,
    interface: str,
    password: str | None = None,
    hidden: bool = False,
    runner: NMCLIRunner = _run_nmcli,
) -> str:
    ssid = ssid.strip()
    if not ssid:
        raise WifiLoginError("SSID is required.")

    command = ["device", "wifi", "connect", ssid, "ifname", interface]
    if password:
        command.extend(["password", password])
    if hidden:
        command.extend(["hidden", "yes"])
    runner(command)

    connection_name = _resolve_active_connection(interface, runner=runner)
    runner(
        [
            "connection",
            "modify",
            connection_name,
            "connection.interface-name",
            interface,
            "connection.autoconnect",
            "yes",
            "connection.autoconnect-priority",
            AUTOCONNECT_PRIORITY,
        ]
    )
    return connection_name


def _print_networks(
    networks: list[WifiNetwork],
    *,
    interface: str,
    output: OutputFunc,
) -> None:
    if not networks:
        output(f"No visible Wi-Fi networks found on {interface}.")
    else:
        output(f"Available Wi-Fi networks on {interface}:")
        for index, network in enumerate(networks, start=1):
            marker = "*" if network.in_use else " "
            signal = "?" if network.signal is None else str(network.signal)
            output(
                f" {index:>2}. {marker} {network.ssid} "
                f"[{network.display_security}] signal {signal}"
            )
    output("  h. Hidden SSID")
    output("  r. Rescan")
    output("  q. Quit")


def _prompt_hidden_network(
    *,
    interface: str,
    input_fn: InputFunc,
    password_fn: PasswordFunc,
    output: OutputFunc,
    runner: NMCLIRunner,
) -> int:
    ssid = input_fn("Hidden SSID: ").strip()
    if not ssid:
        output("SSID is required.")
        return 1

    password = password_fn("Password (leave blank for an open network): ")
    try:
        connection_name = connect_wifi_network(
            ssid=ssid,
            interface=interface,
            password=password or None,
            hidden=True,
            runner=runner,
        )
    except WifiLoginError as exc:
        output(f"Failed to configure '{ssid}' on {interface}: {exc}")
        return 1

    output(
        f"Connected '{ssid}' on {interface}. "
        f"Autoconnect is enabled on '{connection_name}'."
    )
    return 0


def run_interactive(
    *,
    interface: str,
    runner: NMCLIRunner = _run_nmcli,
    input_fn: InputFunc = input,
    output: OutputFunc = print,
    password_fn: PasswordFunc = getpass.getpass,
) -> int:
    while True:
        try:
            networks = scan_wifi_networks(interface, runner=runner)
        except WifiLoginError as exc:
            output(f"Unable to scan Wi-Fi networks on {interface}: {exc}")
            return 1

        _print_networks(networks, interface=interface, output=output)
        choice = input_fn(
            "Pick a network number, 'h' for hidden, 'r' to rescan, or 'q' to quit: "
        ).strip().lower()

        if choice == "q":
            output("Cancelled Wi-Fi login.")
            return 0
        if choice == "r":
            continue
        if choice == "h":
            hidden_result = _prompt_hidden_network(
                interface=interface,
                input_fn=input_fn,
                password_fn=password_fn,
                output=output,
                runner=runner,
            )
            if hidden_result == 0:
                return 0
            continue

        try:
            selected_index = int(choice)
        except ValueError:
            output("Enter a listed number, 'h', 'r', or 'q'.")
            continue

        if selected_index < 1 or selected_index > len(networks):
            output("Selection is out of range.")
            continue

        network = networks[selected_index - 1]
        if security_is_enterprise(network.security):
            output(
                f"'{network.ssid}' uses enterprise Wi-Fi security "
                f"({network.security}), which this script does not configure."
            )
            continue

        password = None
        if security_requires_password(network.security):
            password = password_fn(f"Password for {network.ssid}: ")
            if not password:
                output("A password is required for this network.")
                continue

        try:
            connection_name = connect_wifi_network(
                ssid=network.ssid,
                interface=interface,
                password=password,
                runner=runner,
            )
        except WifiLoginError as exc:
            output(f"Failed to configure '{network.ssid}' on {interface}: {exc}")
            continue

        output(
            f"Connected '{network.ssid}' on {interface}. "
            f"Autoconnect is enabled on '{connection_name}'."
        )
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively scan Wi-Fi networks, connect one on a device, "
            "and enable autoconnect for the resulting NetworkManager profile."
        )
    )
    parser.add_argument(
        "--interface",
        default="wlan1",
        help="Wireless interface to scan and configure. Defaults to wlan1.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_interactive(interface=args.interface)


__all__ = (
    "WifiLoginError",
    "WifiNetwork",
    "build_parser",
    "connect_wifi_network",
    "main",
    "parse_wifi_scan_output",
    "run_interactive",
    "scan_wifi_networks",
    "security_is_enterprise",
    "security_requires_password",
)
