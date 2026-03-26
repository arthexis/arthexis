from __future__ import annotations

from django.core.management.base import CommandError


def _has_legacy_action_flags(options: dict[str, object]) -> bool:
    """Return whether any legacy action flag is present."""

    return (
        options.get('rename') is not None
        or bool(options.get('rfid_disable'))
        or bool(options.get('rfid_enable'))
        or bool(options.get('rfid_lockdown'))
        or bool(options.get('send_local_rfids'))
        or bool(options.get('send_restart'))
        or bool(options.get('send_stop'))
        or options.get('sessions') is not None
        or options.get('tail') is not None
        or bool(options.get('ws_auth_clear'))
        or bool((options.get('ws_auth_password') or '').strip())
        or bool((options.get('ws_auth_username') or '').strip())
    )


def resolve_action(options: dict[str, object]) -> dict[str, object]:
    """Resolve action from subcommands first, then legacy compatibility flags."""

    subcommand = options.get('action')
    if subcommand and _has_legacy_action_flags(options):
        raise CommandError(
            'Do not combine verb-style subcommands with legacy action flags.'
        )
    if subcommand == 'show':
        return {'name': 'show'}
    if subcommand == 'tail':
        return {'name': 'tail', 'count': options.get('count')}
    if subcommand == 'sessions':
        return {'name': 'sessions', 'count': options.get('count')}
    if subcommand == 'rfid':
        return {'name': 'rfid', 'mode': options.get('rfid_action')}
    if subcommand == 'auth':
        auth_action = options.get('auth_action')
        if auth_action == 'set':
            return {
                'name': 'auth_set',
                'password': options.get('password'),
                'username': (options.get('username') or '').strip(),
            }
        return {'name': 'auth_clear'}
    if subcommand == 'rename':
        return {'name': 'rename', 'value': options.get('name')}
    if subcommand == 'stop':
        return {'name': 'stop'}
    if subcommand == 'restart':
        return {'name': 'restart'}

    return _resolve_legacy_action(options)


def _resolve_legacy_action(options: dict[str, object]) -> dict[str, object]:
    """Resolve the requested action using backward-compatible long flags."""

    if options.get('rfid_lockdown') and options.get('send_local_rfids'):
        raise CommandError(
            '--rfid-lockdown already sends local RFIDs; remove --send-local-rfids.'
        )

    ws_auth_username = (options.get('ws_auth_username') or '').strip()
    ws_auth_password = options.get('ws_auth_password')
    if options.get('ws_auth_clear') and (ws_auth_username or ws_auth_password):
        raise CommandError(
            'Use either --ws-auth-clear or '
            '--ws-auth-username/--ws-auth-password, not both.'
        )

    actions: list[dict[str, object]] = []
    if options.get('rename') is not None:
        actions.append({'name': 'rename', 'value': options.get('rename')})
    if options.get('rfid_disable'):
        actions.append({'name': 'rfid', 'mode': 'off'})
    if options.get('rfid_enable'):
        actions.append({'name': 'rfid', 'mode': 'on'})
    if options.get('rfid_lockdown'):
        actions.append({'name': 'rfid', 'mode': 'lock'})
    if options.get('send_local_rfids'):
        actions.append({'name': 'rfid', 'mode': 'push'})
    if options.get('send_restart'):
        actions.append({'name': 'restart'})
    if options.get('send_stop'):
        actions.append({'name': 'stop'})
    if options.get('sessions') is not None:
        actions.append({'name': 'sessions', 'count': options.get('sessions')})
    if options.get('tail') is not None:
        actions.append({'name': 'tail', 'count': options.get('tail')})
    if options.get('ws_auth_clear'):
        actions.append({'name': 'auth_clear'})
    if ws_auth_username or ws_auth_password:
        actions.append(
            {
                'name': 'auth_set',
                'password': ws_auth_password,
                'username': ws_auth_username,
            }
        )

    if len(actions) > 1:
        raise CommandError('Choose one charger action at a time.')
    if actions:
        return actions[0]
    return {'name': 'show'}
