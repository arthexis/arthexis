# Agent Card v1 RFID layout

This document describes the older compact Agent Card v1 proposal. For the
current local reader/writer transport, LCD label, writer metadata, and trait
layout, see [Arthexis RFID card layout](rfid-card-layout.md).

Agent Card v1 is a compact MIFARE Classic 1K layout for carrying enough card
identity and capability references to start a simple suite-assisted workflow
when Arthexis already has the rest of the ruleset. The card is not a script
bundle, credential store, or secure enclave. It is a human-auditable handle
that lets a trusted reader and the Arthexis suite decide what the card may
activate.

See also the [RFID scanner service](../services/rfid-scanner-service.md) and
the [sigil command reference](sigil-script-command.md).

## Goals

- Store one manifest, up to four registered-card identity slots, and ten
  capability or small-file slots on one 1K RFID card.
- Keep every application slot readable as one short ASCII record, padded to the
  sector payload size.
- Use SIGILS for suite-known capabilities instead of storing credentials or raw
  commands on the card.
- Treat the physical keycard reader location as scan-event context, not as a
  card-stored authority claim.
- Reject malformed slots, unknown slot types, arbitrary scripts, credentials,
  and unrestricted URLs before activation.

## Non-goals

- Do not store full rulesets, private keys, API tokens, passwords, or executable
  code on the card.
- Do not make the card a general-purpose programmable surface.
- Do not rely on MIFARE Classic keys as the only security boundary for
  activation.
- Do not let the card assert that it is at the top operator console or another
  privileged location.

## Physical layout

MIFARE Classic 1K has 16 sectors. Sectors 1-15 each have three 16 byte data
blocks and one 16 byte trailer block for keys and access bits. Sector 0 has one
read-only manufacturer block, two 16 byte data blocks, and one trailer block.
Agent Card v1 reserves all of sector 0 so application data never depends on
manufacturer or UID handling. Trailer blocks are not application data.

| Sector | Data bytes | Agent Card v1 use |
| --- | ---: | --- |
| 0 | 0 | Reserved for manufacturer/UID and suite transport handling. |
| 1 | 48 | Manifest slot. |
| 2 | 48 | Registered-card identity slot 1. |
| 3 | 48 | Registered-card identity slot 2. |
| 4 | 48 | Registered-card identity slot 3. |
| 5 | 48 | Registered-card identity slot 4. |
| 6-15 | 480 | Ten slots for skill SIGILS, file references, checksums, or short notes. |

The v1 budget is exactly 720 bytes of application payload:

| Payload group | Slots | Bytes per slot | Total bytes |
| --- | ---: | ---: | ---: |
| Manifest | 1 | 48 | 48 |
| Registered-card identity | 4 | 48 | 192 |
| Capability or file slots | 10 | 48 | 480 |
| **Total** | **15** | **48** | **720** |

## Slot grammar

Each application sector stores one ASCII record of at most 48 bytes. Writers pad
the rest of the 48 byte payload with spaces (`0x20`). Readers trim trailing
spaces, parse the record, and reject anything that exceeds 48 bytes or includes
non-printable control characters.

All records start with `AC1|`, followed by an allowlisted slot code:

| Slot code | Allowed sectors | Meaning |
| --- | --- | --- |
| `M` | 1 | Manifest. |
| `I1` through `I4` | 2-5 | Registered-card identity slots. |
| `K01` through `K10` | 6-15 | Skill or capability SIGIL slot. |
| `F01` through `F10` | 6-15 | Small file reference, checksum, or short note slot. |

Records use `KEY=VALUE` fragments separated by `|`. Values should be concise,
uppercase where practical, and drawn from allowlisted alphabets for the field.
No field is interpreted as a command.

Example records:

```text
AC1|M|S=4|X=10|ALG=B2S8|POL=RDRSIG
AC1|I1|NS=CARD|ID=7G4P2K|H=3MF4DA8C2E1B
AC1|I2|VOID=1
AC1|K01|SIG=[AGENT.SKILL:TRIAGE]|H=A91B22
AC1|F02|T=NOTE|H=A91B22|TXT=BRIEF-HINT
```

The manifest is intentionally small. It declares the version, expected identity
and extension-slot counts, hash or checksum algorithm, and activation policy.
The fixed sector map is the directory; each occupied slot must still identify
itself by slot code so accidental writes are detectable.

An empty or intentionally unused identity sector must be encoded as
`AC1|I<n>|VOID=1`, where `<n>` is the slot number for that sector. It must not
be arbitrary blank data, so the reader can distinguish an unused slot from a
damaged or uninitialized sector.

## Capability and file slots

The ten extension slots are for compact references organized by the manifest and
fixed sector map. They may contain:

- SIGILS that map to suite-known skills or card actions.
- Short file or registry references.
- Checksums or compact notes useful during operator-assisted workflows.

## Activation flow

1. Reader detects the card UID and emits a signed scan event.
2. Suite verifies the reader, node, freshness, nonce, and trust tier.
3. Reader or provisioning tool reads sectors 1-15 and returns raw 48 byte slot
   payloads.
4. Suite validates the manifest, sector map, slot grammar, and checksums.
5. Suite resolves only allowlisted SIGILS for the current operator identity and
   trust tier.
6. Suite activates only registered capabilities that match the resolved SIGILS
   and task context.

Any failure before capability resolution leaves the card in preview or rejection
state. Unknown slot types, malformed records, credential-like payloads, or
script-like payloads are hard rejections.

## Writing and rotation

Card writers should write whole sectors, then read back and validate the same
grammar before declaring success. Sector 0 stays reserved. Sector trailers are
for card transport keys and access bits only.

Provisioning should support:

- Creating a new manifest and empty identity/capability slots.
- Setting or rotating sector keys per deployment policy.
- Updating one identity or extension slot at a time with read-back validation.
- Revoking a card UID or manifest fingerprint in the suite registry.
- Marking cards as preview-only when sector reads fail or checksums drift.

Because MIFARE Classic is cloneable and has known weaknesses, high-trust actions
must rely on suite registry state, reader-event proof, freshness checks, and
operator identity. The card alone is never enough for privileged activation.

## Parser and writer contract

Future implementation should expose a small service boundary rather than ad hoc
parsing in views or scripts:

```text
parse_agent_card(sector_payloads) -> AgentCardManifest
validate_reader_event(event) -> ReaderTrustResult
plan_agent_activation(card, reader_event, operator) -> ActivationPlan
write_agent_card_slot(card_uid, sector, record, writer_context) -> WriteResult
```

The parser should be deterministic and reject by default. The writer should use
the same parser after read-back validation.

## Test contract

Implementation work should add fixtures and tests for:

- Valid manifest with four identity slots and ten extension slots.
- Short cards, overlong records, wrong sector codes, unknown slot types, and
  non-printable control characters.
- Empty identity slots versus uninitialized blank sectors.
- Reader trust tiers, stale timestamps, nonce reuse, missing proof, and unknown
  reader IDs.
- Capability SIGILS that are known, unknown, disabled, or not allowed for the
  current operator.
