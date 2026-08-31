# USB Inventory

USB inventory is a Control-node local service that records attached USB block
devices and maps them to local claim roles such as removable media workflows.
Claims live in a host-local JSON file, not in suite fixtures, so device serials
and operator-specific role bindings stay off the shared source tree.

## Control-node boundary

The `usb-inventory` node feature is assigned to the `Control` role fixture and
auto-detection also checks the local node role at runtime. Non-Control nodes do
not auto-enable the feature and the `sensors usb-inventory` command refuses to
run on them.

The feature also requires Linux `lsblk` and `findmnt` commands. Hosts without
those tools do not auto-detect the feature.

## Commands

Refresh inventory:

```bash
python manage.py sensors usb-inventory refresh
```

List current inventory:

```bash
python manage.py sensors usb-inventory list
```

Resolve a claimed role to mounted paths:

```bash
python manage.py sensors usb-inventory claimed-path --role kindle-postbox
```

Resolve a mounted path to matching claims:

```bash
python manage.py sensors usb-inventory path-claims /media/kindle
```

Kindle Postbox uses the `kindle-postbox` claim to find mounted Kindle roots when
copying generated suite documentation. Keep those match rules in the local
claims file rather than hardcoding device paths in the docs sync command.

## Imager Media Exclusions

USB inventory marks Raspberry Pi image partitions as imager media when their
labels match `bootfs` or `rootfs`. It also marks the configured SD-card burner
from `IMAGER_GWAY_BURN_DEVICE` or `IMAGER_BURN_DEVICE`, including partitions
below that disk. Kindle Postbox and bastion unlock claims do not match imager
media, even when a broad local claim would otherwise match the filesystem.

The default local paths are `/etc/arthexis-usb/claims.json` for claims and
`/run/arthexis-usb/devices.json` for generated state. Override them with
`USB_INVENTORY_CLAIMS_PATH` and `USB_INVENTORY_STATE_PATH` in Django settings.

## Control-Node Timer Cadence

Control nodes can share one USB tree between Realtek Wi-Fi uplinks, removable
storage, USB audio, and the bastion unlock key. The product-managed systemd
timer overrides therefore avoid the old 10-second polling cadence by default:

- `arthexis-usb-inventory.timer`: `OnBootSec=2min`,
  `OnUnitActiveSec=5min`, `RandomizedDelaySec=30s`.
- `bastion-usb-refresh.timer`: `OnBootSec=3min`,
  `OnUnitActiveSec=10min`, `RandomizedDelaySec=60s`.

The timers are intentionally staggered so inventory scans and bastion unlock
mount/remount/sync work do not repeatedly collide on the same USB bus. The
bastion refresh service still exits immediately while
`/run/bastion-ssh/refresh.disabled` exists, and site udev rules may start it
on explicit USB add/remove events for faster key responsiveness.

Arthexis writes these cadence settings to its own systemd drop-in,
`10-arthexis-control-usb-polling.conf`, so operator-owned drop-ins such as
`override.conf` are not overwritten or deleted by suite lifecycle scripts.

Operators who need faster polling can set environment overrides before running
`install.sh` or `configure.sh` in systemd Control-node mode:

- `ARTHEXIS_USB_INVENTORY_TIMER_ON_BOOT_SEC`
- `ARTHEXIS_USB_INVENTORY_TIMER_ON_UNIT_ACTIVE_SEC`
- `ARTHEXIS_USB_INVENTORY_TIMER_RANDOMIZED_DELAY_SEC`
- `ARTHEXIS_BASTION_USB_REFRESH_TIMER_ON_BOOT_SEC`
- `ARTHEXIS_BASTION_USB_REFRESH_TIMER_ON_UNIT_ACTIVE_SEC`
- `ARTHEXIS_BASTION_USB_REFRESH_TIMER_RANDOMIZED_DELAY_SEC`
