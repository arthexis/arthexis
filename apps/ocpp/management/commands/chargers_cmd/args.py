from __future__ import annotations

import argparse


def add_selector_arguments(parser: argparse.ArgumentParser) -> None:
    """Register shared charger selector options on ``parser``."""

    parser.add_argument(
        '--sn',
        dest='serial',
        help=(
            'Serial number (or suffix) used to narrow the charger selection. '
            'Matching is case-insensitive and falls back to helpful suffix '
            'matching.'
        ),
    )
    parser.add_argument(
        '-cp',
        '--cp',
        dest='cp',
        help=(
            'Connector identifier used to filter chargers. Provide a connector '
            "number or 'all' to select all connector charge points. Non-numeric "
            'values fall back to matching the charge point path, ignoring '
            'surrounding slashes.'
        ),
    )
    parser.add_argument(
        '--default-base',
        action='store_true',
        help='Select the first available base charger when no selector is provided.',
    )


def build_selector_parent() -> argparse.ArgumentParser:
    """Build a parent parser with shared charger selector options."""

    parent = argparse.ArgumentParser(add_help=False)
    add_selector_arguments(parent)
    return parent


def build_chargers_parser(parser: argparse.ArgumentParser) -> None:
    """Configure command args, including legacy flags and verb subcommands."""

    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.epilog = (
        "Examples:\n"
        "  charger show --sn CP-01\n"
        "  charger tail 50 --sn CP-01 --cp A\n"
        "  charger sessions 10 --sn CP-01\n"
        "  charger rfid on --sn CP-01\n"
        "  charger auth set cp-user secret123 --sn CP-01\n"
        "  charger rename 'Main Hub' --sn CP-01\n"
        "  charger stop --sn CP-01\n"
        "  charger restart --sn CP-01\n"
        "\n"
        "Legacy flags such as --tail, --sessions, --rfid-enable, "
        "--ws-auth-clear,\n"
        "--rename, --send-stop, and --send-restart remain available during "
        "the transition."
    )

    selector_parent = build_selector_parent()
    add_selector_arguments(parser)

    parser.add_argument(
        '--tail',
        dest='tail',
        type=int,
        nargs='?',
        const=20,
        help='Legacy alias for "tail". Show the last N log entries.',
    )
    parser.add_argument(
        '--sessions',
        dest='sessions',
        type=int,
        nargs='?',
        const=10,
        help='Legacy alias for "sessions". Show the last N session logs.',
    )
    parser.add_argument(
        '--rfid-enable',
        action='store_true',
        help='Legacy alias for "rfid on".',
    )
    parser.add_argument(
        '--rfid-disable',
        action='store_true',
        help='Legacy alias for "rfid off".',
    )
    parser.add_argument(
        '--send-local-rfids',
        action='store_true',
        help='Legacy alias for "rfid push".',
    )
    parser.add_argument(
        '--rfid-lockdown',
        action='store_true',
        help='Legacy alias for "rfid lock".',
    )
    parser.add_argument(
        '--ws-auth-username',
        dest='ws_auth_username',
        help='Legacy alias for "auth set <username> <password>" username.',
    )
    parser.add_argument(
        '--ws-auth-password',
        dest='ws_auth_password',
        help='Legacy alias for "auth set <username> <password>" password.',
    )
    parser.add_argument(
        '--ws-auth-clear',
        action='store_true',
        help='Legacy alias for "auth clear".',
    )
    parser.add_argument(
        '--rename',
        nargs='?',
        const='',
        help='Legacy alias for "rename <name>".',
    )
    parser.add_argument(
        '--send-stop',
        action='store_true',
        help='Legacy alias for "stop".',
    )
    parser.add_argument(
        '--send-restart',
        action='store_true',
        help='Legacy alias for "restart".',
    )

    subparsers = parser.add_subparsers(dest='action')

    subparsers.add_parser(
        'show',
        parents=[selector_parent],
        help='Show charger details or the default table view.',
        description='Show charger details or the default table view.',
    )

    tail_parser = subparsers.add_parser(
        'tail',
        parents=[selector_parent],
        help='Show the last N charger log entries.',
        description='Show the last N charger log entries.',
    )
    tail_parser.add_argument('count', type=int, nargs='?', default=20)

    sessions_parser = subparsers.add_parser(
        'sessions',
        parents=[selector_parent],
        help='Show the last N charger sessions.',
        description='Show the last N charger sessions.',
    )
    sessions_parser.add_argument('count', type=int, nargs='?', default=10)

    rfid_parser = subparsers.add_parser(
        'rfid',
        parents=[selector_parent],
        help='Manage RFID requirements and local lists.',
        description='Manage RFID requirements and local lists.',
    )
    rfid_parser.add_argument('rfid_action', choices=['on', 'off', 'open', 'strict', 'push', 'lock'])

    auth_parser = subparsers.add_parser(
        'auth',
        help='Manage websocket basic auth for chargers.',
        description='Manage websocket basic auth for chargers.',
    )
    auth_subparsers = auth_parser.add_subparsers(dest='auth_action', required=True)
    auth_set = auth_subparsers.add_parser('set', parents=[selector_parent])
    auth_set.add_argument('username')
    auth_set.add_argument('password')
    auth_subparsers.add_parser('clear', parents=[selector_parent])

    rename_parser = subparsers.add_parser(
        'rename',
        parents=[selector_parent],
        help='Rename a charger display name.',
        description='Rename a charger display name.',
    )
    rename_parser.add_argument('name', nargs='?', default='')

    subparsers.add_parser(
        'stop',
        parents=[selector_parent],
        help='Send a remote stop request.',
        description='Send a remote stop request.',
    )
    subparsers.add_parser(
        'restart',
        parents=[selector_parent],
        help='Send a soft reset request.',
        description='Send a soft reset request.',
    )
