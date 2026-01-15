# Suite Services Report

The Suite Services Report (Admin → System → Suite Services Report) summarizes the systemd units that can run an Arthexis node. It shows live unit status when `systemctl` is available and also lists **reference services** even when they are not configured so operators can see what is available in the suite.

## Services in the report

| Service | Systemd unit name | Purpose | Enable/disable guide |
| --- | --- | --- | --- |
| [Suite service](services/suite-service.md) | `{service-name}.service` | Primary Django/ASGI process that serves the Arthexis application. | [Suite service](services/suite-service.md) |
| [Celery worker](services/celery-worker.md) | `celery-{service-name}.service` | Background task processor for email, upgrades, and other async work. | [Celery worker](services/celery-worker.md) |
| [Celery beat](services/celery-beat.md) | `celery-beat-{service-name}.service` | Scheduler that triggers periodic Celery tasks. | [Celery beat](services/celery-beat.md) |
| [LCD screen](services/lcd-screen.md) | `lcd-{service-name}.service` | 16x2 LCD updater service for Control nodes. | [LCD screen](services/lcd-screen.md) |
| [RFID scanner service](services/rfid-scanner-service.md) | `rfid-{service-name}.service` | UDP-backed RFID scanner service for local reads. | [RFID scanner service](services/rfid-scanner-service.md) |

## Reading the report

- **Not configured** means the lock file for that service was not enabled on this node (or the suite service name is missing), so the row is included purely for reference.
- **Not found** indicates a unit name is configured but systemd cannot locate the unit on the host.
- **Enabled** reports the systemd enablement state when systemd is available; otherwise it is left blank.

## Related operator docs

- [Install & Lifecycle Scripts Manual](development/install-lifecycle-scripts-manual.md)
- [LCD Screen Hardware](lcd-screen-hardware.md)
- [Auto-Upgrade Flow](auto-upgrade.md)
