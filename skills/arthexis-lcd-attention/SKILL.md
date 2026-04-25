---
name: arthexis-lcd-attention
description: Write a concise high-priority attention message to the local Arthexis 16x2 LCD. Use when Codex has been processing for a while, completes a long-running task, or needs the user to return for confirmation or a manual step. Format line 1 as a very short code title plus the local message time, and line 2 as one or two short hint words describing the required action.
---

# Arthexis Lcd Attention

Use this skill to post a brief, high-signal message on the device LCD after long processing or when waiting on the user. Keep the output safe for a shared screen and concise enough to fit a 16x2 character display.

When user attention is required between Arthexis main phases, use a critical sustained display. The helper writes to the sticky high-priority LCD lock immediately and, when sustained mode is enabled, adds a bounded expiry so the message remains visible while the agent waits without blocking the agent or spawning a refresh loop.

## Workflow

1. Choose a code title of about 3-8 uppercase characters that summarizes the state: `DONE`, `WAIT`, `TEST`, `SYNC`, `FIX`.
2. Choose one or two imperative hint words for the required action: `REVIEW`, `CHECK LOGS`, `SCAN QR`, `APPROVE`.
3. For ordinary completion/status attention, run `scripts/post_attention.py --code <CODE> --hint <WORD> [--action <WORD>]`.
4. For approval or manual input required between main phases, run:

```bash
python3 scripts/post_attention.py --code WAIT --hint APPROVE --critical --sustained
```

5. Use `--print-only` while testing or when you only need the rendered 16x2 preview.

The helper writes the high-priority LCD lock directly under the configured Arthexis base directory. Sustained messages are bounded by the lock-file expiry line and the script returns after the initial atomic write.

## Format

- Put the short code title and local `HH:MM` on line 1.
- Put one or two short hint words on line 2.
- Prefer uppercase ASCII and simple words that are readable from a distance.
- Avoid secrets, tokens, stack traces, filenames, or detailed status text.
- Avoid using this for ordinary short turns when the user is already actively watching the session.
- Keep sustained attention bounded. The default sustained duration is 600 seconds; no background worker is started, and the LCD runner drops the message when the lock expiry passes.

## Commands

```bash
python3 scripts/post_attention.py --code DONE --hint REVIEW
python3 scripts/post_attention.py --code TEST --hint CHECK --action LOGS
python3 scripts/post_attention.py --code WAIT --hint APPROVE --print-only
python3 scripts/post_attention.py --code WAIT --hint APPROVE --critical --sustained
python3 scripts/post_attention.py --code WAIT --hint APPROVE --critical --sustain-seconds 120 --interval 10
```

## Notes

- Default Arthexis base dir: `/home/arthe/arthexis`
- If you need more message ideas, read `references/message-patterns.md`
