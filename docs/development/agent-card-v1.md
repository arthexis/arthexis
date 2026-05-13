# Agent Card v1 RFID layout

This document describes the older compact Agent Card v1 proposal. For the
current local reader/writer transport, LCD label, writer metadata, and trait
layout, see [Arthexis RFID card layout](rfid-card-layout.md).

Agent Card v1 is a compact MIFARE Classic 1K layout for carrying enough
identity and capability references to start a simple agent when the suite
already has the rest of the ruleset. The card is not a script bundle, credential
store, or secure enclave. It is a human-auditable handle that lets a trusted
reader and the Arthexis suite decide what the card may activate.

See also the [RFID scanner service](../services/rfid-scanner-service.md) and
the [sigil command reference](sigil-script-command.md).

## Goals

- Store one manifest, up to four Soul identity seed slots, and ten capability or
  small-file slots on one 1K RFID card.
- Keep every application slot readable as one short ASCII record, padded to the
  sector payload size.
- Use SIGILS for suite-known capabilities instead of storing credentials or raw
  commands on the card.
- Treat the physical keycard reader location as scan-event context, not as a
  card-stored authority claim.
- Reject malformed slots, unknown slot types, arbitrary scripts, credentials,
  and unrestricted URLs before activation.

## Non-goals

- Do not store full Souls, rulesets, private keys, API tokens, passwords, or
  executable code on the card.
- Do not make the card a general-purpose programmable surface.
- Do not rely on MIFARE Classic keys as the only security boundary for agent
  activation.
- Do not let the card assert that it is at the top operator console or another
  privileged location.

## Physical layout

MIFARE Classic 1K has 16 sectors. Sectors 1-15 each have three 16 byte
data blocks and one 16 byte trailer block for keys and access bits. Sector 0
has one read-only manufacturer block, two 16 byte data blocks, and one trailer
block. Agent Card v1 reserves all of sector 0 so application data never depends
on manufacturer or UID handling. Trailer blocks are not application data.

| Sector | Data bytes | Agent Card v1 use |
| --- | ---: | --- |
| 0 | 0 | Reserved for manufacturer/UID and suite transport handling. |
| 1 | 48 | Manifest slot. |
| 2 | 48 | Soul identity seed 1. |
| 3 | 48 | Soul identity seed 2. |
| 4 | 48 | Soul identity seed 3. |
| 5 | 48 | Soul identity seed 4. |
| 6-15 | 480 | Ten slots for skill SIGILS, file references, checksums, or short notes. |

The v1 budget is exactly 720 bytes of application payload:

| Payload group | Slots | Bytes per slot | Total bytes |
| --- | ---: | ---: | ---: |
| Manifest | 1 | 48 | 48 |
| Soul identity seeds | 4 | 48 | 192 |
| Capability or file slots | 10 | 48 | 480 |
| **Total** | **15** | **48** | **720** |

There is no unassigned application sector left on a 1K card after this layout.
Sector keys can vary per sector, but keys and access bits do not add application
payload. The card data model spans sectors, not key slots.

## Slot grammar

Each application sector stores one ASCII record of at most 48 bytes. Writers pad
the rest of the 48 byte payload with spaces (`0x20`). Readers trim trailing
spaces, parse the record, and reject anything that exceeds 48 bytes or includes
non-printable control characters.

All records start with `AC1|`, followed by an allowlisted slot code:

| Slot code | Allowed sectors | Meaning |
| --- | --- | --- |
| `M` | 1 | Manifest. |
| `I1` through `I4` | 2-5 | Soul identity seed slots. |
| `K01` through `K10` | 6-15 | Skill or capability SIGIL slot. |
| `F01` through `F10` | 6-15 | Small file reference, checksum, or short note slot. |

Records use `KEY=VALUE` fragments separated by `|`. Values should be concise,
uppercase where practical, and drawn from allowlisted alphabets for the field.
No field is interpreted as a command.

Example records:

```text
AC1|M|S=4|X=10|ALG=B2S8|POL=RDRSIG
AC1|I1|NS=SOUL|ID=7G4P2K|H=3MF4DA8C2E1B
AC1|I2|VOID=1
AC1|K01|SIG=[AGENT.SKILL:TRIAGE]|H=A91B22
AC1|F02|T=NOTE|H=A91B22|TXT=BRIEF-HINT
```

The manifest is intentionally small. It declares the version, expected identity
and extension-slot counts, hash or checksum algorithm, and activation policy.
The fixed sector map is the directory; each occupied slot must still identify
itself by slot code so accidental writes are detectable.

## Soul identity seeds

Soul seed slots identify which suite-known Soul or agent package the card is
trying to match. They are not the Soul itself. A seed should contain a namespace,
a short human-facing ID, and a compact fingerprint or checksum that the suite can
compare against its registry.

Match score:

| Matching seeds | Score | Use |
| ---: | --- | --- |
| 0 of 4 | No match | Reject unless an operator is only inspecting raw card data. |
| 1 of 4 | Weak | May identify a candidate, but should require more context. |
| 2 of 4 | Plausible | Enough for preview or operator-assisted selection. |
| 3 of 4 | Strong | Suitable for normal activation with trusted reader context. |
| 4 of 4 | Best | Highest confidence match for a task. |

An empty or intentionally unused identity sector must be encoded as
`AC1|I<n>|VOID=1`, where `<n>` is the slot number for that sector. It must not
be arbitrary blank data, so the reader can distinguish an unused slot from a
damaged or uninitialized sector.

## Capability and file slots

The ten extension slots are for compact references organized by the manifest and
fixed sector map. They may contain:

- Skill or capability SIGILS that resolve through Arthexis under the current
  operator identity.
- File references to suite-local, registry-known, or package-known artifacts.
- Checksums for files or skills that live outside the card.
- Short notes that help a human recognize the card.

They must not contain:

- Passwords, API tokens, SSH keys, private keys, or session cookies.
- Shell commands, Python snippets, JavaScript, SQL, or other executable code.
- Unrestricted URLs or remote fetch instructions.
- Policy expressions with branching, loops, or arbitrary variable access.

SIGILS are pointers into the suite. Resolution happens after identity matching,
reader trust validation, and operator-scope checks. If the current operator is
not allowed to resolve a SIGIL, the slot stays inert.

Capability SIGILS must still fit inside one 48 byte sector. `AC1|Kxx|SIG=`
consumes 12 bytes before the SIGIL value, and any checksum metadata consumes
additional bytes. Use short registry aliases for on-card `Kxx` slots. If a
capability needs a longer SIGIL, multiple attributes, or human-readable context,
store a compact `Fxx` file reference or registry handle on the card and keep the
larger artifact in the suite.

## Reader-location trust

The trusted location factor belongs to the keycard reader and node, not the
card. A card cannot claim `LOC=TOP` or similar authority. Instead, the reader or
node emits a scan event that the suite can verify.

Minimum scan event fields:

```json
{
  "card_uid": "04AABBCCDD",
  "reader_id": "top-console-rfid-01",
  "node_id": "gway-001",
  "scan_id": "20260501T201500Z-7K2M",
  "observed_at": "2026-05-01T20:15:00Z",
  "nonce": "base64-or-hex-random",
  "trust_tier": "trusted_operator_console",
  "proof_alg": "node-local-hmac-or-signature",
  "proof": "base64-signature"
}
```

Trust tiers should be explicit:

| Tier | Reader context | Activation behavior |
| --- | --- | --- |
| `unknown` | Reader is not registered or proof is missing. | Parse only; no activation. |
| `local_authenticated` | Reader is registered on a known node. | Preview or require operator confirmation. |
| `trusted_operator_console` | Reader is physically tied to the top operator console. | May supply operator-presence factor. |
| `trusted_gway` | Reader is on a trusted GWAY node over the approved channel. | May supply operator-presence factor for GWAY-scoped actions. |
| `provisioner` | Reader/writer is an approved card provisioning station. | May write or rotate card sectors subject to operator approval. |

Replay protection belongs in the reader event: timestamp freshness, nonce reuse
checks, and a node-local signature or HMAC. Card UID alone is not sufficient.

## Activation flow

1. Reader detects the card UID and emits a signed scan event.
2. Suite verifies the reader, node, freshness, nonce, and trust tier.
3. Reader or provisioning tool reads sectors 1-15 and returns raw 48 byte slot
   payloads.
4. Suite validates the manifest, sector map, slot grammar, and checksums.
5. Suite computes the Soul seed match score against its registry.
6. Suite resolves only allowlisted SIGILS for the current operator identity and
   trust tier.
7. Suite activates only registered capabilities that match the resolved SIGILS
   and task context.

Any failure before step 6 leaves the card in preview or rejection state. Unknown
slot types, malformed records, credential-like payloads, or script-like payloads
are hard rejections.

## Writing and rotation

Card writers should write whole sectors, then read back and validate the same
grammar before declaring success. Sector 0 stays reserved. Sector trailers are
for card transport keys and access bits only.

Provisioning should support:

- Creating a new manifest and empty identity/capability slots.
- Setting or rotating sector keys per deployment policy.
- Updating one Soul seed or extension slot at a time with read-back validation.
- Revoking a card UID or manifest fingerprint in the suite registry.
- Marking cards as preview-only when sector reads fail or checksums drift.

Because MIFARE Classic is cloneable and has known weaknesses, high-trust actions
must rely on suite registry state, reader-event proof, freshness checks, and
operator identity. The card alone is never enough for privileged activation.

## Provisioning command contract

The suite provisioning boundary is the `soul_seed provision` command. It turns
an operator prompt and a card UID into a deterministic Agent Card v1 sector map.
By default it is a dry run and does not write database records or physical card
sectors:

```powershell
python manage.py soul_seed provision --prompt "rfid reader problem" --card-uid AABBCCDD --json
```

Use `--write` to persist the suite registry side of the card:

```powershell
python manage.py soul_seed provision --prompt "rfid reader problem" --card-uid AABBCCDD --write --json
```

Persisted provisioning creates or updates:

- The composed `SoulIntent`, `SkillBundle`, and `AgentInterfaceSpec`.
- The matching `RFID` registry record for the card UID.
- One active `SoulSeedCard` for the card UID, unless the previous card record
  was revoked.
- The card manifest fingerprint and generated sector payload under
  `SoulSeedCard.card_payload`.

The command can write the raw unpadded sector records to a JSON file for a
future hardware writer adapter:

```powershell
python manage.py soul_seed provision --prompt "rfid reader problem" --card-uid AABBCCDD --sectors-json-out sectors.json
```

The JSON response also includes `padded_sector_records`, where every value is
exactly 48 bytes when encoded as ASCII. A physical writer should write those
padded payloads, read sectors 1-15 back, and pass the read-back data through
`parse_agent_card()` before declaring success.

Skill SIGILS that do not fit in one 48 byte sector, or that fail parser safety
checks, are omitted from the card payload and reported in compatibility notes.
They still remain in the suite-side bundle, so the operator can revise the skill
alias or handle them through a future registry indirection instead of storing
unsafe or oversized text on the card.

## Activation command contract

The console activation boundary is the `soul_seed activate` command. It does not
write card sectors or start a long-running daemon. It resolves an already
provisioned `SoulSeedCard`, validates the stored Agent Card payload fingerprint,
and creates or closes a `CardSession` for one suite console:

```powershell
python manage.py soul_seed activate --card-uid AABBCCDD --console-id terminal-1 --json
```

Reader adapters can pass scan output as JSON instead of extracting a UID
themselves. The payload must contain `card_uid`, `uid`, or `rfid`:

UID-only activation is intentionally enabled for local console flows. When a
reader proof is unavailable, activation still proceeds with trust tier
`unknown` so operators can bootstrap or recover a console session. Deployments
that require stronger assurance should run this boundary behind a trusted local
adapter that injects `reader_id` and an explicit trust tier.

```powershell
python manage.py soul_seed activate --scan-json scan.json --console-id terminal-1 --reader-id desk-reader --json
```

Activation returns only the bounded session payload needed to render a CLI or UI:

- Session identity, console identity, reader identity, trust tier, and runtime
  namespace.
- Card UID, RFID label id, and manifest fingerprint.
- Intent summary and risk metadata.
- Skill bundle slug, selected skill slugs, tool allowlist, compatibility notes,
  and fallback guidance.
- Interface mode, schema, commands, suggestions, and visible fields.

Session semantics are intentionally deterministic:

- Presenting a different active card at the same console evicts the previous
  active session and clears its runtime namespace and activation plan.
- Presenting the same active card at the same console closes the session and
  clears runtime state.
- Passing `--timeout-seconds` evicts this console's stale active sessions before
  starting a new activation.
- `soul_seed evict-stale --console-id terminal-1 --timeout-seconds 300` can be
  used by a future daemon or watchdog to evict unattended sessions.

## Parser and writer contract

Future implementation should expose a small service boundary rather than ad hoc
parsing in views or scripts:

```text
parse_agent_card(sector_payloads) -> AgentCardManifest
score_soul_identity(card, registry_candidates) -> MatchScore
validate_reader_event(event) -> ReaderTrustResult
plan_agent_activation(card, reader_event, operator) -> ActivationPlan
write_agent_card_slot(card_uid, sector, record, writer_context) -> WriteResult
```

The parser should be deterministic and reject by default. The writer should use
the same parser after read-back validation.

## Test contract

Implementation work should add fixtures and tests for:

- Valid manifest with four Soul seed slots and ten extension slots.
- Short cards, overlong records, wrong sector codes, unknown slot types, and
  non-printable control characters.
- Empty identity slots versus uninitialized blank sectors.
- Match scoring for 0/4, 1/4, 2/4, 3/4, and 4/4 seeds.
- Reader trust tiers, stale timestamps, nonce reuse, missing proof, and unknown
  reader IDs.
- Rejection of credential-like payloads, script-like payloads, and unrestricted
  URLs.
- SIGIL resolution staying scoped to the current operator and allowed trust tier.
