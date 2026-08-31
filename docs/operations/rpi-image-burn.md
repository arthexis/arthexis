# Raspberry Pi Image Burn Runbook

Use this runbook when an SD card is on a burner and the goal is a field-ready
Raspberry Pi image that can be reached over SSH after the first boot without
manual Raspberry Pi OS setup.

## Operator checklist

1. Power off the target Raspberry Pi and remove the SD card.
2. Insert the SD card in the burner attached to the build or control node.
3. Build or select a current image that includes recovery SSH and any required
   network profiles.
4. Confirm the image is smaller than the target card.
5. Inspect the burner device and unmount any mounted partitions.
6. Write the image with the suite writer or the control-node wrapper.
7. Wait for write verification to finish.
8. Inspect the burned card while it is still on the burner.
9. Move the card back to the target Pi and power the Pi before running SSH or
   HTTP access tests.

Do not run post-boot SSH, HTTP, or reservation tests while the card is still in
the burner. Those tests only become meaningful after the card is in the target
Pi and the Pi is powered.

## Build the image

### Initial field profile

An image may include one private TOML profile for idempotent gway field setup.
The profile is copied into the image with mode `0600`; it must not be committed
when it contains field card identifiers, charger identities, redirect addresses,
or site Wi-Fi selections.

```toml
[node]
number = 4

[network]
copy_host_profiles = ["SITE_WIFI_ONE", "SITE_WIFI_TWO"]

[rfid]
pre_register = ["RFID_ONE", "RFID_TWO"]
fallback_account = true

[charger]
id = "CHARGER_SERIAL"
path = "/ocpp-j/CHARGER_SERIAL"
connectors = [1]

[auto_start]
id_tag = "TALLER"

[ocpp_redirect]
interface = "eth0"
charger_ip = "192.0.2.10"
targets = ["198.51.100.10"]
target_port = 80
listen_port = 8888
```

Pass it only to a customized build:

```bash
./manage.py imager build ... --initial-profile /secure/path/initial-profile.toml
```

At build time, `node.number` becomes the reservation number and named
`network.copy_host_profiles` entries are copied from the build host's
NetworkManager store. The profile never contains Wi-Fi credentials itself.

After migrations, first boot runs `manage.py imager initial-profile --apply --profile
...`. The command defaults to parse-only validation when `--apply` is omitted,
which is safe to run on the image build host. With `--apply`, it creates missing RFID rows, an optional fallback service account, an
auto-start service account, and absent strict charger rows; existing
RFID/account assignments and a different existing auto-start tag are never
overwritten. If `[ocpp_redirect]` is present, it
installs only the declared `eth0`/source-IP/destination-IP/port nftables rule
and enables a service to restore that exact rule after reboot. The image adds
`nftables` only for profiles containing that section.

Keep `authorization_policy=open` out of field profiles. Use a known charger
identity, strict policy, the declared RFID registrations, and the OCPP idTag
service account instead.

For GWAY images, include `[node] number = N`. New `rfid.pre_register` entries
then receive the node's default label range: `N000`, `N010`, and so on. For
example, gway-004 receives `4000` and `4010`. Existing RFID records are left
unchanged, and a conflicting reserved label stops first-boot reconciliation.

For a normal GWAY field box, build from the suite checkout that should be baked
into the image. The recovery key is required for customized builds unless the
image is intentionally disposable.

```bash
.venv/bin/python manage.py imager build \
  --name gway-004-v0-10-0-20260604 \
  --base-image-uri build/rpi-imager/base/2025-05-13-raspios-bookworm-arm64-lite.img \
  --suite-source /home/arthe/arthexis \
  --reserve \
  --reserve-number 4 \
  --recovery-ssh-user arthe \
  --recovery-authorized-key-file /home/arthe/.ssh/rpi-putty-key.pub \
  --copy-parent-network \
  --minimum-image-size-gib 7
```

Recommended defaults:

- Use `--reserve --reserve-number N` when the box has a known field identity
  such as `gway-004`.
- Use `--reserve-prefix gway` when building from a non-GWAY parent node so the
  reserved hostname is `gway-00N` instead of inheriting the build host prefix.
- Reserved field images, including `gway-NNN` nodes, enable Raspberry Pi
  Connect bootstrap by default. Pass `--skip-connect-bootstrap` only when the
  image intentionally must not install Raspberry Pi Connect.
- Pass `--connect-auth-key-file /mnt/bastion-unlock/bastion/api-tokens/tokens/rpi-connect-auth.toml`
  only when the image should sign into Raspberry Pi Connect on first boot. The
  source file must be mode `0600` and may be either a raw key or TOML containing
  `[rpi_connect] auth_key = "..."`. The bootstrap removes the embedded key file
  after the sign-in attempt.
- Use `--copy-parent-network` for the active upstream Wi-Fi profile when that
  is all the device needs.
- Use `--copy-host-network NAME` for each additional site profile the device
  should know.
- Use `--copy-all-host-networks` only on trusted build hosts where every saved
  NetworkManager profile and credential is appropriate for the image.
- Keep recovery SSH enabled with `--recovery-authorized-key-file` or
  `--recovery-authorized-key`.
- Avoid `--skip-recovery-ssh` for field cards unless another recovery lane has
  already been proven.

For routine Control-node burns, prefer `imager gway-burn`; it reserves a
`gway-00N` identity and enables Raspberry Pi Connect bootstrap by default. The
command uses local offline numbering unless `--next-number-base-url` or
`IMAGER_GWAY_REGISTRATION_BASE_URL` is explicitly set. First-boot downstream
registration is separate: set `--downstream-registration-base-url` or
`IMAGER_DOWNSTREAM_REGISTRATION_BASE_URL` only when the image should call that
upstream after boot. Pass `--skip-connect-bootstrap` only for images that
intentionally should not install RPi Connect.

## Size the image for the card

Raw image size must be less than or equal to the target device size. Many cards
sold as 8 GB are smaller than an 8 GiB raw image.

```bash
python3 -c "import os; print(os.path.getsize('build/rpi-imager/gway-004-v0-10-0-20260604-rpi-4b.img'))"
.venv/bin/python manage.py imager devices
```

The writer refuses to burn an image larger than the target. When using common
8 GB SD cards, prefer a 7 GiB minimum image size unless the exact card capacity
has already been proven. Use a larger card for an 8 GiB image.

## Inspect the burner

List candidate devices before every burn:

```bash
.venv/bin/python manage.py imager devices
```

Accept a target only when the row shows:

- the expected device path, for example `/dev/sda`
- `removable=yes`
- `protected=no`
- `mounts=(none)`
- target capacity greater than or equal to the image size

Example ready target:

```text
/dev/sda size=8086618112 transport=usb removable=yes protected=no partitions=/dev/sda1 mounts=(none)
```

If any target partition is mounted, unmount every mounted partition under the
target device before writing:

```bash
sudo umount /dev/sda1 /dev/sda2
```

Some control nodes intentionally auto-mount specific USB media for other
operator workflows. Pause that owner temporarily rather than repeatedly racing
the mount. After the burn and card inspection, restore the paused service.

## Write through the suite

Use `--artifact` when the image build registered an artifact:

```bash
.venv/bin/python manage.py imager write \
  --artifact gway-004-v0-10-0-20260604 \
  --device /dev/sda \
  --yes
```

Use `--image-path` when writing a local raw image directly:

```bash
.venv/bin/python manage.py imager write \
  --image-path build/rpi-imager/gway-004-v0-10-0-20260604-rpi-4b.img \
  --device /dev/sda \
  --yes
```

On Linux Control nodes, destructive suite writes pause local USB pollers and
desktop disk monitors by default for the write and verification window. This
reduces automount, USB inventory, bastion refresh, Kindle postbox, and
display-layout races while the writer is doing heavy SD-card I/O. Pass
`--no-quiet-usb` only when an operator has a specific reason to keep those local
services active during the burn. Durable burner jobs use the same quieting
behavior.

The writer performs these safeguards:

- refuses protected system disks
- refuses mounted target partitions
- refuses targets smaller than the source image
- requires explicit `--yes`
- verifies the written byte range with SHA-256

Do not interrupt the write after it starts. Wait for verification to finish.

## Use the control-node wrapper

On control nodes that provide `/home/arthe/burn-rpi-image.sh`, use the wrapper
for a preflight plus workgroup logging:

```bash
/home/arthe/burn-rpi-image.sh \
  --check \
  --device /dev/sda \
  --workgroup BurnGate
```

Then run the write:

```bash
/home/arthe/burn-rpi-image.sh \
  --image-path /home/arthe/arthexis/build/rpi-imager/gway-004-v0-10-0-20260604-rpi-4b.img \
  --device /dev/sda \
  --workgroup BurnGate \
  --yes
```

Current Control-node installs also place persistent USB stability udev rules
when the imager burner service is enabled:

- common Realtek USB Wi-Fi uplinks are kept out of autosuspend
- a configured `IMAGER_GWAY_BURN_DEVICE` or `IMAGER_BURN_DEVICE` by-id burner
  path is marked `UDISKS_IGNORE=1` so desktop automount paths leave it alone

Wrapper options:

- `--check`: validate the target and source selection without writing.
- `--image-path PATH`: write a raw uncompressed `.img` file.
- `--artifact NAME`: resolve and write a registered suite artifact.
- `--device PATH`: select the target block device; default control-node target
  is usually `/dev/sda`.
- `--checkout PATH`: select the suite checkout; default is
  `/home/arthe/arthexis`.
- `--workgroup NAME`: refresh the matching workgroup user and record burn
  events.
- `--yes`: required for the destructive write.

The wrapper rejects compressed handoff files such as `.img.xz`, `.zip`, `.gz`,
`.bz2`, and `.zst`. Decompress or build a raw image first.

## Verify the burned card

After the write completes, inspect the card before moving it back to the Pi.
The inspection should confirm the reservation, bootstrap script, suite bundle,
network profiles, and recovery SSH files.

Expected signals for a field-ready card:

- `NODE_HOSTNAME` matches the target, for example `gway-004`.
- `bootstrap_executable` is true.
- `firstrun_executable` is true.
- `firstrun_has_recovery_hook` is true.
- `recovery_authorized_keys_present` is true.
- `recovery_script_executable` is true.
- `recovery_service_enabled` is true.
- `recovery_sshd_config_present` is true.
- `suite_bundle_size` is greater than zero.
- `network_profile_count` matches the intended copied profiles.

Also confirm the boot partition has `userconf.txt` for the recovery user and an
`ssh` marker. Those files skip Raspberry Pi OS first-user setup and enable SSH
on first boot.

## Post-boot access test

Only after the card is installed in the target Pi and the Pi is powered, test
SSH and suite HTTP:

```bash
.venv/bin/python manage.py imager test-access \
  --host 10.42.0.4 \
  --ssh-user arthe \
  --ssh-key ~/.ssh/id_ed25519 \
  --http-url http://10.42.0.4:8888/login/
```

Use `--skip-http` while the suite is still bootstrapping. Use `--skip-ssh` only
when intentionally validating an HTTP-only deployment path.

## Burner option matrix

Choose the least broad burner path that matches the physical setup:

| Option | Use when | Notes |
| --- | --- | --- |
| Suite `imager write --artifact` | The build completed normally and registered an artifact. | Best for routine burns because artifact metadata is available. |
| Suite `imager write --image-path` | A raw image exists locally but was not registered. | Verify the image was already customized; write does not add recovery SSH. |
| `burn-rpi-image.sh --check` | A control-node operator wants a no-write preflight. | Confirms removable, unprotected, unmounted media and source size. |
| `burn-rpi-image.sh --image-path` | A control node has the card writer and a local raw image. | Adds workgroup logging and rejects compressed handoff images. |
| `burn-rpi-image.sh --artifact` | A control node should write a registered artifact. | Uses the configured checkout to resolve the artifact. |

## Troubleshooting

If the target keeps remounting, identify the service that owns the mount and
pause it for the burn window. Rerun `imager devices` and proceed only when the
target shows `mounts=(none)`.

If upstream Wi-Fi degrades only while a USB3 burner is active, compare a normal
USB3 burn-stress run with one booted under the Realtek `rtw88_usb`
`switch_usb_mode=N` setting. That setting forces USB2 mode to avoid possible
2.4 GHz interference, but it also reduces bus bandwidth, so it should be adopted
per hardware profile only after packet loss, beacon loss, USB reset, and burn
throughput are compared.

If the write fails with a size error, compare the image byte count with the
device byte count. Rebuild with a smaller `--minimum-image-size-gib` or use a
larger SD card.

If the Pi shows the Raspberry Pi OS first-user setup screen, the burned image
does not have a valid boot-partition `userconf.txt`. Rebuild with recovery SSH
key provisioning and verify `userconf.txt` before moving the card to the Pi.

If `guestfish` or `supermin` fails on an ARM control node, confirm that the
libguestfs appliance is using a kernel supported by the host CPU. On nodes with
both Raspberry Pi kernels and Debian kernels installed, force the Debian kernel
for the build session:

```bash
export SUPERMIN_KERNEL=/boot/vmlinuz-6.1.0-48-arm64
export SUPERMIN_MODULES=/lib/modules/6.1.0-48-arm64
export SUPERMIN_KERNEL_VERSION=6.1.0-48-arm64
```

Then rerun the build. Use the installed Debian kernel version on the host if it
differs from the example above.
