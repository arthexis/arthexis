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
  --download-base-uri https://downloads.example.com/images
```

Accepted base image inputs include:

- raw `.img` files
- compressed `.img.xz` and `.img.gz` files
- `.zip` archives that contain a single image file

When bootstrap customization is enabled, the build now keeps `guestfish` temp and cache files under `build/rpi-imager` instead of relying on `/var/tmp`.

List registered artifacts:

```bash
.venv/bin/python manage.py imager list
```

## 2) Discover candidate target devices

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

## 3) Write an artifact to removable media

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
