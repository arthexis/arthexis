#!/usr/bin/env python
"""Access point watchdog service.

This module intentionally avoids touching Django models or settings. It relies on
shell utilities (systemctl, nmcli, ping) and lock/template files stored under
``.locks`` to understand which services should be online and how to reconfigure
network connections.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
LOCK_DIR = BASE_DIR / ".locks"
LOG_DIR = BASE_DIR / "logs"
TEMPLATE_PATH = LOCK_DIR / "ap_watchdog_template.json"
LOCK_FILE = LOCK_DIR / "ap_watchdog.lck"
LOG_FILE = LOG_DIR / "ap-watchdog.log"
SYSTEMD_LOCK = LOCK_DIR / "systemd_services.lck"
SERVICE_LOCK = LOCK_DIR / "service.lck"
SERVICE_MODE_LOCK = LOCK_DIR / "service_mode.lck"


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner:
    def run(self, args: Iterable[str], check: bool = False) -> CommandResult:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, proc.args, proc.stdout, proc.stderr
            )
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)


@dataclass
class ConnectionTemplate:
    name: str
    interface: str
    conn_type: str
    mode: str = ""


@dataclass
class APWatchdog:
    base_dir: Path
    runner: CommandRunner = field(default_factory=CommandRunner)
    downtime: dict[str, int] = field(default_factory=dict)
    restart_failures: dict[str, int] = field(default_factory=dict)
    template: list[ConnectionTemplate] = field(default_factory=list)
    ap_connection: str | None = None

    def __post_init__(self) -> None:
        self.lock_dir = self.base_dir / ".locks"
        self.log_dir = self.base_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "ap-watchdog.log"
        self.template_path = self.lock_dir / "ap_watchdog_template.json"
        self.service_lock = self.lock_dir / "service.lck"
        self.systemd_lock = self.lock_dir / "systemd_services.lck"
        self.service_mode_lock = self.lock_dir / "service_mode.lck"
        self.template = self._load_template()
        self.ap_connection = self._detect_ap_connection()

    def log(self, message: str | None) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(message or ".")
            handle.write("\n" if message else "")

    def _run(self, args: Iterable[str]) -> CommandResult:
        return self.runner.run(args)

    def _load_template(self) -> list[ConnectionTemplate]:
        if not self.template_path.exists():
            return []
        try:
            data = json.loads(self.template_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            self.log(f"Error loading template file {self.template_path}: {e}")
            return []
        templates = []
        for item in data.get("connections", []):
            try:
                templates.append(
                    ConnectionTemplate(
                        name=item["name"],
                        interface=item.get("interface", ""),
                        conn_type=item.get("type", ""),
                        mode=item.get("mode", ""),
                    )
                )
            except KeyError:
                self.log(
                    f"Skipping invalid connection template item without 'name': {item}"
                )
                continue
        return templates

    def _detect_ap_connection(self) -> str | None:
        for template in self.template:
            if template.mode.lower() == "ap":
                return template.name
        # Fallback: query nmcli if template missing
        result = self._run(
            ["nmcli", "-t", "-f", "NAME,802-11-wireless.mode", "connection", "show"]
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if not line:
                continue
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue
            if parts[1].strip() == "ap":
                return parts[0]
        return None

    def _service_mode(self) -> str:
        if self.service_mode_lock.exists():
            return self.service_mode_lock.read_text(encoding="utf-8").strip().lower()
        return "embedded"

    def _expected_services(self) -> tuple[str | None, list[str]]:
        main_service = None
        if self.service_lock.exists():
            main_service = self.service_lock.read_text(encoding="utf-8").strip()
        expected: list[str] = []
        if self.systemd_lock.exists():
            expected = [
                line.strip()
                for line in self.systemd_lock.read_text().splitlines()
                if line.strip()
            ]
        elif main_service:
            expected = [f"{main_service}.service"]
        return main_service, expected

    def _is_active(self, unit: str) -> bool:
        result = self._run(["systemctl", "is-active", "--quiet", unit])
        return result.returncode == 0

    def _restart_unit(self, unit: str) -> bool:
        result = self._run(["sudo", "systemctl", "restart", unit])
        return result.returncode == 0

    def _restart_stack(self, main_service: str, units: list[str]) -> list[str]:
        actions: list[str] = []
        order = [unit for unit in units if unit == f"{main_service}.service"]
        order += [unit for unit in units if unit != f"{main_service}.service"]
        for unit in order:
            if not unit:
                continue
            if self._restart_unit(unit):
                actions.append(f"Restarted {unit}")
            else:
                actions.append(f"Failed to restart {unit}")
        return actions

    def _check_services(self) -> list[str]:
        actions: list[str] = []
        main_service, expected_units = self._expected_services()
        if not expected_units:
            return actions
        for unit in expected_units:
            active = self._is_active(unit)
            if active:
                self.downtime[unit] = 0
                self.restart_failures[unit] = 0
                continue
            self.downtime[unit] = self.downtime.get(unit, 0) + 1
            if self.downtime[unit] < 4:
                continue
            self.downtime[unit] = 0
            if main_service and unit == f"{main_service}.service":
                actions.extend(self._restart_stack(main_service, expected_units))
            else:
                if self._restart_unit(unit):
                    actions.append(f"Restarted {unit}")
                else:
                    actions.append(f"Failed to restart {unit}")
            if unit not in self.restart_failures:
                self.restart_failures[unit] = 0
            self.restart_failures[unit] += 1
            if not self._is_active(unit) and self.restart_failures[unit] >= 4:
                self._run(["sudo", "reboot", "now"])
                actions.append("Reboot triggered after repeated failures")
        return actions

    def _active_connections(self) -> list[tuple[str, str, str]]:
        result = self._run(
            ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"]
        )
        if result.returncode != 0:
            return []
        active: list[tuple[str, str, str]] = []
        for line in result.stdout.splitlines():
            if not line:
                continue
            parts = line.split(":")
            if len(parts) >= 3:
                active.append((parts[0], parts[1], parts[2]))
        return active

    def _ping_via(self, interface: str) -> bool:
        ping_targets = ["8.8.8.8", "1.1.1.1", "9.9.9.9"]
        for target in ping_targets:
            result = self._run(["ping", "-I", interface, "-c", "1", "-W", "2", target])
            if result.returncode == 0:
                return True
        return False

    def _switch_interface(self, name: str, interface: str) -> None:
        self._run(
            ["nmcli", "connection", "modify", name, "connection.interface-name", interface]
        )

    def _bring_connection_up(self, name: str, interface: str | None = None) -> None:
        cmd = ["nmcli", "connection", "up", name]
        if interface:
            cmd.extend(["ifname", interface])
        self._run(cmd)

    def _bring_connection_down(self, name: str) -> None:
        self._run(["nmcli", "connection", "down", name])

    def _restore_template(self) -> list[str]:
        actions: list[str] = []
        for template in self.template:
            if not template.interface:
                continue
            self._switch_interface(template.name, template.interface)
            actions.append(f"Restored {template.name} to {template.interface}")
        return actions

    def _connections_on_interface(self, interface: str) -> list[ConnectionTemplate]:
        return [template for template in self.template if template.interface == interface]

    def _handle_networks(self) -> list[str]:
        actions: list[str] = []
        active = self._active_connections()
        wlan1_active = any(device == "wlan1" for _, device, _ in active)
        has_internet = wlan1_active and self._ping_via("wlan1")
        ap_name = self.ap_connection
        if not ap_name and self.template:
            ap_name = self._detect_ap_connection()
        if has_internet:
            if ap_name:
                self._switch_interface(ap_name, "wlan0")
                self._bring_connection_up(ap_name, "wlan0")
                actions.append(f"Ensured AP {ap_name} active on wlan0")
            actions.extend(self._restore_template())
            return actions

        if ap_name:
            self._bring_connection_down(ap_name)
            actions.append(f"Disabled AP {ap_name} due to missing wlan1 connectivity")

        for template in self._connections_on_interface("wlan1"):
            self._switch_interface(template.name, "wlan0")
            self._bring_connection_up(template.name, "wlan0")
            actions.append(f"Moved {template.name} to wlan0")
        return actions

    def run_once(self) -> None:
        actions = []
        actions.extend(self._check_services())
        actions.extend(self._handle_networks())
        if actions:
            for action in actions:
                self.log(action)
        else:
            self.log(None)

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(60)


def snapshot_nmcli_template(base_dir: Path = BASE_DIR, runner: CommandRunner | None = None) -> list[ConnectionTemplate]:
    run = runner.run if runner else CommandRunner().run
    result = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    connections: list[ConnectionTemplate] = []
    if result.returncode != 0:
        print(
            f"Warning: Failed to snapshot nmcli connections: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return connections
    for line in result.stdout.splitlines():
        if not line:
            continue
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        name, conn_type = parts
        iface_result = run(
            ["nmcli", "-g", "connection.interface-name", "connection", "show", name]
        )
        interface = iface_result.stdout.strip() if iface_result.returncode == 0 else ""
        mode = ""
        if conn_type.strip().lower() in {"wifi", "802-11-wireless"}:
            mode_result = run(
                ["nmcli", "-g", "802-11-wireless.mode", "connection", "show", name]
            )
            if mode_result.returncode == 0:
                mode = mode_result.stdout.strip()
        connections.append(
            ConnectionTemplate(
                name=name,
                interface=interface,
                conn_type=conn_type.strip(),
                mode=mode,
            )
        )
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "connections": [
            {
                "name": item.name,
                "interface": item.interface,
                "type": item.conn_type,
                "mode": item.mode,
            }
            for item in connections
        ],
    }
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    template_path = lock_dir / "ap_watchdog_template.json"
    template_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lock_file = lock_dir / "ap_watchdog.lck"
    lock_file.write_text("enabled\n", encoding="utf-8")
    return connections


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arthexis AP watchdog")
    parser.add_argument("--snapshot", action="store_true", help="Refresh template only")
    parser.add_argument(
        "--run-once", action="store_true", help="Execute a single iteration and exit"
    )
    args = parser.parse_args(argv)

    if args.snapshot:
        snapshot_nmcli_template()
        return 0

    watchdog = APWatchdog(BASE_DIR)
    if args.run_once:
        watchdog.run_once()
    else:
        watchdog.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
