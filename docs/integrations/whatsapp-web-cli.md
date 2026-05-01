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

Not supported in this version:

- Polling or background message ingestion.
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
python manage.py whatsapp send --to 528119218587 --message "Hello from Arthexis"
```

Read messages visible in a phone-number chat:

```powershell
python manage.py whatsapp read --from 528119218587 --json
```

Read messages from one date:

```powershell
python manage.py whatsapp read --from 528119218587 --date 2026-05-01 --json
```

Read only messages after the local cursor and then advance that cursor:

```powershell
python manage.py whatsapp read --from 528119218587 --new --json
```

Attach to an already-open Edge debugging session:

```powershell
python manage.py whatsapp send --to 528119218587 --message "Hello" --cdp-url http://127.0.0.1:9223
```

## Privacy And Side Effects

The command targets one requested phone-number chat. It does not scan unrelated
chat lists for content. The `read` action extracts visible message text from
the opened chat only.

Opening a chat in WhatsApp Web can mark messages as read in the WhatsApp
account. Use `read --new --no-update-cursor` to inspect output without advancing
the local Arthexis cursor, but WhatsApp's own read state may still change.

The `--new` cursor is local to the browser profile and phone number. It is not a
poller and does not run in the background.
