# Desktop shortcuts operations runbook

Arthexis desktop workflows now rely on explicit, app-owned commands and typed models.
Legacy extension-to-command execution has been retired.

## Supported operator workflow

Use the desktop shortcut synchronizer to render `.desktop` launchers from
`DesktopShortcut` records:

```bash
.venv/bin/python manage.py sync_desktop_shortcuts --username <local-user> --port <suite-port>
```

Example:

```bash
.venv/bin/python manage.py sync_desktop_shortcuts --username arthexis --port 8000
```

## Operational notes

- Manage launch targets and conditions through **Admin → Desktop → Desktop Shortcuts**.
- Model integration behaviors in the owning Django app and expose dedicated
  management commands there.
- Do not reintroduce generic file-extension command execution; route operator
  workflows through maintained commands and model-backed admin surfaces.

## Upgrade mapping for retired commands

Use `sync_desktop_shortcuts` as the supported replacement for both retired commands:

- `desktop_extension_open` → `sync_desktop_shortcuts`
- `register_desktop_extensions` → `sync_desktop_shortcuts`

Run the replacement command. It can auto-detect the local username and suite port,
or you can provide them explicitly when needed:

```bash
.venv/bin/python manage.py sync_desktop_shortcuts [--username <local-user>] [--port <suite-port>]
```
