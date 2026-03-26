from __future__ import annotations

from django.core.management.base import CommandError
from django.db.models import QuerySet

from apps.ocpp.models import Charger


class ChargersActionRunner:
    """Dispatch and execute charger command actions."""

    def __init__(self, command):
        self.command = command
        self._handlers = {
            'auth_clear': self._auth_clear,
            'auth_set': self._auth_set,
            'rename': self._rename,
            'restart': self._restart,
            'rfid': self._rfid,
            'sessions': self._sessions,
            'show': self._show,
            'stop': self._stop,
            'tail': self._tail,
        }

    def execute(
        self,
        *,
        action: dict[str, object],
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        selection: dict[str, object],
    ) -> None:
        name = str(action['name'])
        handler = self._handlers.get(name)
        if handler is None:
            raise CommandError(f'Unknown charger action: {name}')
        handler(action=action, chargers=chargers, queryset=queryset, selection=selection)

    def _show(
        self,
        *,
        action: dict[str, object],
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        selection: dict[str, object],
    ) -> None:
        if selection['serial'] or selection['cp_raw']:
            self.command.renderer.render_details(chargers)
            return
        self.command.renderer.render_table(chargers)

    def _tail(
        self,
        *,
        action: dict[str, object],
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        selection: dict[str, object],
    ) -> None:
        count = self.command._validate_positive_count(action.get('count'), '--tail')
        self.command._require_selector(
            selection,
            message=(
                'Log tail requires selecting at least one charger with --sn '
                'and/or --cp.'
            ),
        )
        if len(chargers) != 1:
            raise CommandError(
                '--tail requires selecting exactly one charger using --sn and/or --cp.'
            )
        self.command.renderer.render_details(chargers)
        self.command.renderer.render_tail(chargers[0], count)

    def _sessions(
        self,
        *,
        action: dict[str, object],
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        selection: dict[str, object],
    ) -> None:
        count = self.command._validate_positive_count(action.get('count'), '--sessions')
        self.command.renderer.render_sessions(chargers, count)

    def _auth_set(
        self,
        *,
        action: dict[str, object],
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        selection: dict[str, object],
    ) -> None:
        self.command._require_selector(
            selection,
            message=(
                'Websocket auth changes require selecting at least one '
                'charger with --sn and/or --cp.'
            ),
        )
        self.command._handle_auth_set(
            chargers=chargers,
            password=action.get('password'),
            queryset=queryset,
            username=str(action.get('username') or ''),
        )

    def _auth_clear(
        self,
        *,
        action: dict[str, object],
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        selection: dict[str, object],
    ) -> None:
        self.command._require_selector(
            selection,
            message=(
                'Websocket auth changes require selecting at least one '
                'charger with --sn and/or --cp.'
            ),
        )
        updated = queryset.update(ws_auth_user=None, ws_auth_group=None)
        self.command.stdout.write(
            self.command.style.SUCCESS(
                f'Cleared websocket auth protection on {updated} charger(s).'
            )
        )

    def _rfid(
        self,
        *,
        action: dict[str, object],
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        selection: dict[str, object],
    ) -> None:
        self.command._require_selector(selection)
        self.command._handle_rfid_action(
            chargers=chargers,
            mode=str(action['mode']),
            queryset=queryset,
        )

    def _rename(
        self,
        *,
        action: dict[str, object],
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        selection: dict[str, object],
    ) -> None:
        self.command._require_selector(selection)
        self.command._handle_rename(chargers=chargers, value=action.get('value'))

    def _stop(
        self,
        *,
        action: dict[str, object],
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        selection: dict[str, object],
    ) -> None:
        self.command._require_selector(selection)
        sent = self.command._send_stop(chargers)
        self.command.stdout.write(
            self.command.style.SUCCESS(f'Sent remote stop request to {sent} charger(s).')
        )

    def _restart(
        self,
        *,
        action: dict[str, object],
        chargers: list[Charger],
        queryset: QuerySet[Charger],
        selection: dict[str, object],
    ) -> None:
        self.command._require_selector(selection)
        restart_targets = self.command._action_targets_for_single_station(chargers)
        sent = self.command._send_restart(restart_targets)
        self.command.stdout.write(
            self.command.style.SUCCESS(f'Sent reset request to {sent} charger(s).')
        )
