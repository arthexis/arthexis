from __future__ import annotations

from apps.nmcli.wifi_login import WifiNetwork, parse_wifi_scan_output, run_interactive


def test_parse_wifi_scan_output_deduplicates_visible_networks():
    output = """
IN-USE:
SSID: Office
SECURITY: WPA2
SIGNAL: 30

IN-USE: *
SSID: Office
SECURITY: WPA2
SIGNAL: 70

IN-USE:
SSID:
SECURITY: WPA2
SIGNAL: 80

IN-USE:
SSID: Guest
SECURITY: --
SIGNAL: 45
""".strip()

    assert parse_wifi_scan_output(output) == [
        WifiNetwork(ssid="Office", security="WPA2", signal=70, in_use=True),
        WifiNetwork(ssid="Guest", security="", signal=45, in_use=False),
    ]


def test_run_interactive_connects_selected_network_and_sets_autoconnect():
    commands: list[list[str]] = []
    messages: list[str] = []
    prompts = iter(["1"])

    def fake_runner(args):
        command = list(args)
        commands.append(command)
        if command[:5] == [
            "-m",
            "multiline",
            "-f",
            "IN-USE,SSID,SECURITY,SIGNAL",
            "device",
        ]:
            return """
IN-USE:
SSID: Office
SECURITY: WPA2
SIGNAL: 70
""".strip()
        if command == ["-g", "GENERAL.CONNECTION", "device", "show", "wlan1"]:
            return "Office\n"
        return ""

    exit_code = run_interactive(
        interface="wlan1",
        runner=fake_runner,
        input_fn=lambda _prompt: next(prompts),
        output=messages.append,
        password_fn=lambda _prompt: "supersecret",
    )

    assert exit_code == 0
    assert commands == [
        [
            "-m",
            "multiline",
            "-f",
            "IN-USE,SSID,SECURITY,SIGNAL",
            "device",
            "wifi",
            "list",
            "ifname",
            "wlan1",
            "--rescan",
            "auto",
        ],
        [
            "device",
            "wifi",
            "connect",
            "Office",
            "ifname",
            "wlan1",
            "password",
            "supersecret",
        ],
        ["-g", "GENERAL.CONNECTION", "device", "show", "wlan1"],
        [
            "connection",
            "modify",
            "Office",
            "connection.interface-name",
            "wlan1",
            "connection.autoconnect",
            "yes",
            "connection.autoconnect-priority",
            "100",
        ],
    ]
    assert messages[-1] == "Connected 'Office' on wlan1. Autoconnect is enabled on 'Office'."


def test_run_interactive_supports_hidden_networks():
    commands: list[list[str]] = []
    messages: list[str] = []
    prompts = iter(["h", "Backhaul"])

    def fake_runner(args):
        command = list(args)
        commands.append(command)
        if command[:5] == [
            "-m",
            "multiline",
            "-f",
            "IN-USE,SSID,SECURITY,SIGNAL",
            "device",
        ]:
            return ""
        if command == ["-g", "GENERAL.CONNECTION", "device", "show", "wlan1"]:
            return "Backhaul\n"
        return ""

    exit_code = run_interactive(
        interface="wlan1",
        runner=fake_runner,
        input_fn=lambda _prompt: next(prompts),
        output=messages.append,
        password_fn=lambda _prompt: "",
    )

    assert exit_code == 0
    assert commands == [
        [
            "-m",
            "multiline",
            "-f",
            "IN-USE,SSID,SECURITY,SIGNAL",
            "device",
            "wifi",
            "list",
            "ifname",
            "wlan1",
            "--rescan",
            "auto",
        ],
        [
            "device",
            "wifi",
            "connect",
            "Backhaul",
            "ifname",
            "wlan1",
            "hidden",
            "yes",
        ],
        ["-g", "GENERAL.CONNECTION", "device", "show", "wlan1"],
        [
            "connection",
            "modify",
            "Backhaul",
            "connection.interface-name",
            "wlan1",
            "connection.autoconnect",
            "yes",
            "connection.autoconnect-priority",
            "100",
        ],
    ]
    assert messages[-1] == "Connected 'Backhaul' on wlan1. Autoconnect is enabled on 'Backhaul'."
