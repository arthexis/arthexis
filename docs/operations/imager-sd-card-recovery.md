# Imager SD-Card Recovery Workflow

`apps.imager` supports two distinct concerns:

- building Raspberry Pi image artifacts
- safely writing an existing artifact to removable media

This workflow currently focuses on **artifact writing**. It does not perform a live root-disk clone/capture of the running Arthexis node.

## 1) Build an artifact

Build a Raspberry Pi image artifact with first-boot bootstrap scripts:

```bash
.venv/bin/python manage.py imager build \
  --name stable \
  --base-image-uri /path/to/raspios.img.xz \
  --recovery-authorized-key "ssh-ed25519 AAAAC3Nza... operator-laptop" \
  --download-base-uri https://downloads.example.com/images
```

Accepted base image inputs include:

- raw `.img` files
- compressed `.img.xz` and `.img.gz` files
- `.zip` archives that contain a single image file

When bootstrap customization is enabled, the build now keeps `guestfish` temp and cache files under `build/rpi-imager` instead of relying on `/var/tmp`.

Customized builds require recovery SSH keys by default. Use `--skip-recovery-ssh` only when the image is intentionally disposable or another recovery lane exists.

Customized builds also inject a sanitized static source bundle from the current Arthexis checkout into `/usr/local/share/arthexis/arthexis-suite.tar.gz`. On first boot, the bootstrap service extracts that bundle into `/opt/arthexis` and starts the local copy before falling back to `git clone`. Use `--no-bundle-suite` to keep the older clone-on-first-boot behavior, or `--suite-source /path/to/arthexis` to bundle a specific checkout.

To copy host Wi-Fi/Ethernet NetworkManager profiles into the image, pass one or more selected profiles:

```bash
.venv/bin/python manage.py imager build \
  --name field-wifi \
  --base-image-uri /path/to/raspios.img.xz \
  --recovery-authorized-key "ssh-ed25519 AAAAC3Nza... operator-laptop" \
  --copy-host-network "Shop WiFi"
```

Profile selectors match the NetworkManager connection id, filename, or filename stem from `/etc/NetworkManager/system-connections`. Use `--copy-all-host-networks` only when every saved profile and credential on the build host should be copied. For test rigs or nonstandard hosts, override the source directory with `--host-network-profile-dir`.

List registered artifacts:

```bash
.venv/bin/python manage.py imager list
```

## 2) Serve an artifact over HTTP

Serve a registered image artifact from the CLI and persist the advertised URL on the artifact record:

```bash
.venv/bin/python manage.py imager serve \
  --artifact stable \
  --host 0.0.0.0 \
  --port 8090 \
  --url-host 10.42.0.138
```

The command prints `artifact_url=http://10.42.0.138:8090/stable-rpi-4b.img` and blocks until interrupted. Use that URL as the Raspberry Pi Connect image-release `artifact_url` or another deployment-model consumer URL. If an external reverse proxy owns the public path, pass `--base-url https://downloads.example.com/images` instead of `--url-host`.

## 3) Discover candidate target devices

Inspect block devices before writing:

```bash
.venv/bin/python manage.py imager devices
```

This output includes:

- device path and size
- transport and removable indicators
- mountpoints/partitions
- `protected=yes/no` to identify the current system/root disk

The protection check falls back to mountpoint inspection, so hosts that expose the live root disk as `/dev/root` still keep that disk marked as protected.

## 4) Write an artifact to removable media

Write a registered artifact to a target block device:

```bash
.venv/bin/python manage.py imager write \
  --artifact stable \
  --device /dev/sdb \
  --yes
```

You can also write a local image path directly:

```bash
.venv/bin/python manage.py imager write \
  --image-path /tmp/stable-rpi-4b.img \
  --device /dev/sdb \
  --yes
```

## Safety checks before write

The write command refuses dangerous operations when any of these checks fail:

- target device is marked `protected` (system/root disk)
- target device or child partitions are mounted
- target capacity is smaller than image size
- explicit confirmation (`--yes`) is not provided

## Verification behavior

After writing, the workflow verifies the write by computing SHA-256 over:

- the source image file
- the matching byte range written to target media

If checksums differ, the command fails.

When `--artifact` is used, the artifact record stores `metadata.last_write` with:

- device path
- source path
- bytes written
- checksum
- verification timestamp

## Recovery SSH customization

For field recovery images, bake in a key-only SSH lane before writing media. This is the
quick-recovery path to use when an SD card must be reachable over `eth0` before the full
Arthexis bootstrap is healthy:

```bash
.venv/bin/python manage.py imager build \
  --name repair-2026-04-23 \
  --base-image-uri /path/to/raspios.img \
  --recovery-authorized-key "ssh-ed25519 AAAAC3Nza... operator-laptop" \
  --recovery-ssh-user arthe
```

You can still use `--recovery-authorized-key-file /path/to/key.pub` when needed, but passing
`--recovery-authorized-key` directly is recommended for workflows that should avoid bundling credential files in the repo.

What this adds:

- a first-boot recovery user, defaulting to `arthe`
- `authorized_keys` for the provided public key file(s)
- `ssh` enabled and started on first boot
- password login disabled in the generated image's SSH config
- directly enabled systemd units for recovery access and Arthexis bootstrap, so recovery
  SSH does not depend on the slower bootstrap path or on an external writer step

This is intended to give operators a safe default recovery path over the address the device gets on `eth0` before Arthexis finishes bootstrapping.

Recovery SSH key provisioning is required for customized builds unless you intentionally opt out with `--skip-recovery-ssh`.

Then write the recovery-enabled artifact:

```bash
.venv/bin/python manage.py imager write \
  --artifact repair-2026-04-23 \
  --device /dev/sdb \
  --yes
```

If you write with `--image-path`, verify that the image was already built with recovery
metadata or use a freshly built recovery artifact. The write command verifies bytes on
the target media, but it does not retrofit recovery SSH into an arbitrary image.

## Post-install access test

After installing the burned SD card in the Pi and linking it by `eth0` or another network, test recovery SSH and suite HTTP reachability:

```bash
.venv/bin/python manage.py imager test-access \
  --host 10.42.0.50 \
  --ssh-user arthe \
  --ssh-key ~/.ssh/id_ed25519 \
  --http-url http://10.42.0.50:8888/login/
```

Use `--skip-http` while the suite is still bootstrapping, or `--skip-ssh` only when validating an HTTP-only deployment path.
