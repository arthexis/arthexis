# Arthexis RFID card layout

This layout describes the local MIFARE Classic card format used by the suite
reader/writer path.

## Reserved transport sectors

- Sector 0 and sector 1 keep their factory keys and trailers.
- Sector 0 block 0 remains the manufacturer block.
- Sector 0 blocks 1 and 2 store an optional ASCII LCD label as two 16 byte
  lines.
- Sector 1 block 1 stores the writer model or node id.
- Sector 1 block 2 stores the writer timestamp as `YYYYMMDDTHHMMSSZ`.
- Sector 2 is not managed by this layout.

The scanner reads the sector 0 LCD label with the factory key during the fast
scan path so the LCD can show it before database-backed deep reads complete.

## Managed sectors

Managed sectors start at sector 3. When Arthexis initializes a card it generates
random per-sector Key A and Key B values for sectors 3 through 16, stores those
keys on the RFID database record, clears data blocks to zero, and writes the new
trailers. The suite uses Key A for write flows and Key B for read-only flows by
policy while preserving the physical card access bits that keep managed data
readable by the suite.

## Traits

Traits are unordered key/value records over sectors 3 through 16. A trait key is
up to 16 ASCII bytes and a trait value is up to 80 ASCII bytes. Because MIFARE
Classic data blocks are 16 bytes and each small sector only has three data
blocks, one 80 byte trait value spans a sector pair:

| Sector pair data block | Bytes |
| --- | --- |
| Start sector block 0 | 16 byte trait key |
| Start sector block 1 | value bytes 0-15 |
| Start sector block 2 | value bytes 16-31 |
| Continuation sector block 0 | value bytes 32-47 |
| Continuation sector block 1 | value bytes 48-63 |
| Continuation sector block 2 | value bytes 64-79 |

When writing a trait, Arthexis updates the existing key's sector pair when found
or uses the first empty sector pair. The scanner exports decoded traits in the
latest scan lockfile and as `SIGIL_*` names for local transition runners.

## Commands

```bash
.venv/bin/python manage.py rfid init --writer-id WRITER-1
.venv/bin/python manage.py rfid label --line1 "Door Ready" --line2 "Tap card"
.venv/bin/python manage.py rfid trait --key DOOR --value OPEN
```
