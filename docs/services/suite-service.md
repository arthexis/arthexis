# Suite service

## What it is
The Suite service is the primary systemd unit that runs the Arthexis software suite web application (Django/ASGI) on a node. It is the anchor service that other node-scoped companion units (Celery, LCD, RFID) can depend on.

## What it does
- Starts the main application server for the Arthexis suite on that node.
- Provides the base unit name used by companion services (for example, `celery-{service-name}.service`).

## Enable
1. Run the installer with a service name:
   ```bash
   ./install.sh --service <service-name>
   ```
2. Confirm the lock file exists at `.locks/service.lck` and that a systemd unit matching `<service-name>.service` is installed.
3. Start the unit if needed:
   ```bash
   sudo systemctl enable --now <service-name>.service
   ```

## Disable
1. Stop/disable the unit:
   ```bash
   sudo systemctl disable --now <service-name>.service
   ```
2. Remove the lock file if the suite should no longer be managed by systemd:
   ```bash
   rm -f .locks/service.lck
   ```
3. Alternatively, use `./uninstall.sh` to remove all systemd units for the node.

## Notes
- If the suite service name is missing, the Suite Services Report still lists the service rows as **Not configured** for reference.
- On Windows, use `service.bat install --name <service-name>` after running `install.bat`. The helper relies on NSSM to register the Windows service.

## Rename an existing service
Use the rename helper when you need to change the base suite service name for an existing node:

```bash
scripts/rename_service --new-name <new-service-name>
```

Optional flags:
- `--old-name <current-service-name>` to override `.locks/service.lck` detection.
- `--start` to start the renamed units after provisioning.
- `--dry-run` to preview what the script would do.

The helper preserves companion lock selections and renames the managed unit stack (`<name>`, `celery-<name>`, `celery-beat-<name>`, `lcd-<name>`, `rfid-<name>`, `camera-<name>`) when running in systemd mode.
