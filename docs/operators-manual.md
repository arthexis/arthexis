# Operators Manual

The Arthexis operators manual is a curated, Kindle-readable field handbook built
from canonical suite documentation. It is generated as one plain-text file so a
Control node can place the same current manual on every connected Kindle.

## Build The Manual

Generate the operators manual in the local work directory:

```bash
python manage.py docs kindle-postbox build --bundle operators
```

Generate it and publish a copy to a local public library watched by a Kindle
postbox daemon:

```bash
python manage.py docs kindle-postbox build --bundle operators --public-library /home/arthe/Bookshelf
```

Copy it directly to every connected USB target claimed as `kindle-postbox`:

```bash
python manage.py docs kindle-postbox sync --bundle operators --refresh-usb
```

Preview direct Kindle writes without changing removable storage:

```bash
python manage.py docs kindle-postbox sync --bundle operators --refresh-usb --dry-run
```

## Source Manifest

The manual order is controlled by `docs/operators-manual.json`. Each section in
that manifest lists existing documentation files in the order they should appear
in the single generated handbook.

Keep operational facts in their canonical source documents and update the
manifest only when the manual should gain, remove, or reorder a source. The
manual builder fails when a listed source is missing, which keeps stale field
handbooks from being generated silently.

## Kindle Handoff

The generated file is:

```text
work/docs/kindle-postbox/arthexis-operators-manual.txt
```

The generated manifest beside it is:

```text
work/docs/kindle-postbox/arthexis-operators-manual.json
```

When `--public-library` is used, Arthexis copies the generated file only when
the public-library copy differs. Local postbox services can then distribute that
public-library file to connected Kindles without requiring the suite process to
own the long-running USB scan loop.
