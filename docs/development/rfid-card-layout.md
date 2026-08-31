# Arthexis RFID card layout

This layout describes the local MIFARE Classic card format used by the suite
reader/writer path.

## Sector 0 command-card header

- Sector 0 block 0 remains the manufacturer block.
- Sector 0 block 1 stores the card name as up to 16 ASCII bytes. When a card is
  issued from a database template this should be the template's natural key.
- Sector 0 block 2 stores a versioned command-card metadata header.
- Sector 0 block 3 remains the sector trailer.

The metadata block currently uses a compact 16 byte binary header:

| Bytes | Meaning |
| --- | --- |
| 0-3 | Magic `AXC1` |
| 4 | Layout version |
| 5 | Number of command payload data blocks |
| 6 | Number of result data blocks |
| 7 | Flags |
| 8-15 | Reader/writer provenance key |

The scanner reads the name and metadata during the fast scan path so the LCD can
show the card name immediately. Command payloads and result payloads require the
held-card deep-read path.

## Command and result payloads

Command payload data starts at the first managed data block, currently sector 3
block 0, and continues through managed data blocks only, skipping sector
trailers. The metadata command-block count declares where command data ends.
Result data starts at the next data block and uses the remaining managed blocks.
Command-card writers reject payloads that do not leave at least one result block.

The command payload is canonical JSON with one allowlisted suite command and its
parameters:

```json
{"command":"LOG","params":{"channel":"stable"},"sigils":{"SIGIL_MODE":"safe"}}
```

The scanner service never executes raw shell text or `command.sh` payloads from a
card. It dispatches only registered suite command handlers.

Command execution runs as the card owner recorded in the suite database. If the
card has no explicit owner, the command path falls back to the login RFID user
or linked account user when those relationships exist. Commands may declare a
Django permission requirement and are blocked when the resolved user cannot run
them.

Before a valid command handler runs, Arthexis writes a `started` result payload to
the physical card and records an `RFIDCommandExecution` row. After the handler
finishes, Arthexis writes the final result and stores the digest expected on the
card. On the next use, the previous on-card result digest must match the database
record before the command can execute. A mismatch records a blocked execution and
does not run the command.

The provenance key in the metadata header identifies the reader/writer context
that issued the card. In default safe operation, cards that cannot be matched to
known suite database state or whose previous result check fails are recorded but
not executed.

Large result payloads are summarized on-card with a digest and reference id. The
full execution detail remains in the database.

## Legacy managed sectors

The older trait layout still exists for compatibility with already issued cards
and admin tooling. It uses sector pairs from 3 through 14:

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

Legacy trait cards and command cards are different card modes. Traits written
with `rfid trait` are not decoded after a card is rewritten in command-card mode.

Legacy trait card example:

```bash
.venv/bin/python manage.py rfid init --writer-id WRITER-1
.venv/bin/python manage.py rfid label --line1 "Door Ready"
.venv/bin/python manage.py rfid trait --key DOOR --value OPEN
```

Command-card example:

```bash
.venv/bin/python manage.py rfid init --writer-id WRITER-1
.venv/bin/python manage.py rfid command-card write --name "Suite Upgrade" --command LOG --params-json '{"channel":"stable"}'
```
