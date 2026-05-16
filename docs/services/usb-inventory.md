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

The default local paths are `/etc/arthexis-usb/claims.json` for claims and
`/run/arthexis-usb/devices.json` for generated state. Override them with
`USB_INVENTORY_CLAIMS_PATH` and `USB_INVENTORY_STATE_PATH` in Django settings.
