# WhatsApp Web CLI

The `whatsapp` management command provides local, on-demand WhatsApp Web
automation for operator workflows.

This command uses a persistent browser profile. First-time setup requires a
headed browser so the operator can complete WhatsApp Web QR registration.

## Scope

Supported in this version:

- Register or validate a persistent WhatsApp Web login.
- Send one message to one phone number on demand.
- Read visible messages from one phone-number chat on demand.
- Filter read output by `--date`, `--since`, `--until`, or a local `--new`
  cursor.
- Poll an operator self-chat in listener mode and launch a Codex Secretary
  terminal for triggered requests.

Not supported in this version:

- Group chat automation.
- Bulk messaging.
- Attachments or media download.
- Replacing the existing WhatsApp Business API bridge.

## Browser Defaults

- Windows defaults to Microsoft Edge through Playwright's `msedge` channel.
- Linux defaults to Playwright Firefox.

Use `--browser edge`, `--browser firefox`, or `--browser chromium` to override
the default. Use `--channel msedge` when explicitly selecting Edge through
Chromium on Windows.

## Examples

Register or refresh the local WhatsApp Web profile:

```powershell
python manage.py whatsapp login --timeout 300
```

Check the current profile status:

```powershell
python manage.py whatsapp status --json
```

Send one message:

```powershell
python manage.py whatsapp send --to 525551234567 --message "Hello from Arthexis"
```

Read messages visible in a phone-number chat:

```powershell
python manage.py whatsapp read --from 525551234567 --json
```

Read messages from one date:

```powershell
python manage.py whatsapp read --from 525551234567 --date 2026-05-01 --json
```

Read only messages after the local cursor and then advance that cursor:

```powershell
python manage.py whatsapp read --from 525551234567 --new --json
```

Listen for Secretary requests in the operator self-chat:

```powershell
python manage.py whatsapp listen --from 525551234567
```

By default, listener mode waits until the desktop has been idle for 300 seconds,
polls WhatsApp once every 60 seconds, and processes a batch only after 60 seconds
pass without additional new messages. A message must start with `secretary:` to
launch a terminal. Messages that arrive after the first triggered message during
the same quiet batch are included as continuation text.

Run one local dry pass after a quiet batch without launching Codex:

```powershell
python manage.py whatsapp listen --from 525551234567 --once --no-launch --json
```

Plan listener startup provisioning without writing files:

```powershell
python manage.py whatsapp install-listener --from 525551234567
```

Write platform-specific helper files and print the manual install commands:

```powershell
python manage.py whatsapp install-listener --from 525551234567 --write
```

Attach to an already-open Edge debugging session:

```powershell
python manage.py whatsapp send --to 525551234567 --message "Hello" --cdp-url http://127.0.0.1:9223
```

## Listener Startup Provisioning

`install-listener` is a conservative provisioning helper. It builds the exact
`whatsapp listen` command and prints the requirements, generated file paths, and
manual registration commands. It only writes helper files when `--write` is
passed. It does not silently register startup services.

Run `install-listener` on the machine that will run the listener when possible.
When using `--platform` to generate files for a different operating system, the
generated listener command uses target-platform defaults:
`C:\Arthexis\.venv\Scripts\python.exe` plus `C:\Arthexis\manage.py` for Windows,
or `/opt/arthexis/.venv/bin/python` plus `/opt/arthexis/manage.py` for Linux.
Pass `--base-dir`, `--python`, or `--manage-py` when the target machine uses a
different checkout or virtualenv path.

Shared requirements:

- Run `python manage.py whatsapp login --timeout 300` first in a headed browser
  and confirm `python manage.py whatsapp status --json` reports a logged-in
  profile.
- Keep the same `--profile-dir` for `login`, `status`, `listen`, and
  `install-listener`.
- Keep `codex` available on `PATH`, or pass `--codex-command` with the full
  executable path and any fixed flags.
- Use the default `secretary:` trigger unless every new self-chat message should
  create a Secretary request.

Windows provisioning uses an interactive Scheduled Task because Playwright and
WhatsApp Web need the operator desktop session. The generated files are a
PowerShell runner and a registration script. Typical flow:

```powershell
python manage.py whatsapp install-listener --from 525551234567 --write
powershell.exe -NoProfile -ExecutionPolicy Bypass -File '<printed Register-*.ps1 path>'
Start-ScheduledTask -TaskName 'arthexis-whatsapp-listener'
Get-ScheduledTask -TaskName 'arthexis-whatsapp-listener'
```

Windows requirements:

- Microsoft Edge installed.
- Playwright can launch Edge through the `msedge` channel.
- The task runs as the current interactive user at logon, not as a Windows
  service account.

Rollback:

```powershell
Stop-ScheduledTask -TaskName 'arthexis-whatsapp-listener'
Unregister-ScheduledTask -TaskName 'arthexis-whatsapp-listener' -Confirm:$false
```

Linux provisioning writes a shell runner and a systemd user unit. Typical flow:

```bash
python manage.py whatsapp install-listener --from 525551234567 --write
systemctl --user daemon-reload
systemctl --user enable arthexis-whatsapp-listener.service
systemctl --user start arthexis-whatsapp-listener.service
systemctl --user status arthexis-whatsapp-listener.service
```

Linux requirements:

- A graphical user session with `systemd --user`.
- Playwright Firefox installed with `python -m playwright install firefox` when
  needed.
- `loginctl enable-linger <user>` if the listener must survive logout.

Rollback:

```bash
systemctl --user disable --now arthexis-whatsapp-listener.service
```

## Privacy And Side Effects

The command targets one requested phone-number chat. It does not scan unrelated
chat lists for content. The `read` action extracts visible message text from
the opened chat only.

Opening a chat in WhatsApp Web can mark messages as read in the WhatsApp
account. Use `read --new --no-update-cursor` to inspect output without advancing
the local Arthexis cursor, but WhatsApp's own read state may still change.

The `--new` cursor is local to the browser profile path and phone number. It
returns the next visible batch after the stored cursor and advances only after an
explicit on-demand read or after listener mode finishes processing or ignoring a
quiet batch.

Listener mode is intentionally conservative. The default `secretary:` trigger
prevents ordinary self-chat notes from launching local terminals. Use
`--trigger-prefix ""` only when every new self-chat message should become a
Secretary request.
