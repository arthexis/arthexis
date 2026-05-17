# Kindle Postbox

Kindle Postbox generates a plain-text Arthexis suite documentation bundle and
copies it to Kindle storage detected by a Control node.

This is the first suite-owned writer path for field documentation handoff. It
does not probe USB hardware directly; target discovery comes from the USB
inventory claim role `kindle-postbox`, so local serials and mount rules stay in
the host-local claims file described in [USB inventory](usb-inventory.md).

## Commands

Build the current documentation bundle:

```bash
python manage.py docs kindle-postbox build
```

Copy the bundle to every connected USB target claimed as `kindle-postbox`:

```bash
python manage.py docs kindle-postbox sync
```

Refresh USB inventory before resolving connected Kindles:

```bash
python manage.py docs kindle-postbox sync --refresh-usb
```

Preview copy destinations without writing to Kindle storage:

```bash
python manage.py docs kindle-postbox sync --dry-run
```

Copy to an explicit mounted Kindle root:

```bash
python manage.py docs kindle-postbox sync --target /media/kindle
```

## Output

The generated bundle is:

```text
work/docs/kindle-postbox/arthexis-suite-documentation.txt
```

The local manifest beside it records generation time, byte count, and the source
documents included in the bundle:

```text
work/docs/kindle-postbox/arthexis-suite-documentation.json
```

The sync command writes the text bundle to the target `documents/` directory
when it exists, falling back to the mounted root for explicit non-Kindle test
targets.

## Control Node Boundary

`docs kindle-postbox sync` is Control-node-only. Non-Control nodes may build a
local bundle, but they cannot copy it to USB postbox targets. This keeps
removable storage writes isolated to nodes that already own local USB inventory
state.

## Included Sources

The bundle is regenerated from the current checkout each time. It includes:

- `README.md`
- every supported document under `docs/`
- every supported document under `apps/docs/`

Supported formats are Markdown, plain text, CSV, and reStructuredText. The bundle
is intentionally plain text so it remains readable on Kindle devices without
requiring an ebook conversion toolchain.
