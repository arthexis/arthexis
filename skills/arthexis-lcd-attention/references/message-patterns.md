# Message Patterns

Use these patterns when choosing the short code and hint words.

## Examples

- Long task finished, user should look at results: `DONE` + `REVIEW`
- Tests finished, user should inspect failures or logs: `TEST` + `CHECK LOGS`
- Work is blocked pending explicit approval: `WAIT` + `APPROVE`
- QR or badge action is needed at the device: `SCAN` + `SCAN QR`
- Sync or deployment completed and needs spot-checking: `SYNC` + `CHECK ADMIN`

## Style

- Prefer codes that fit in 3-8 characters.
- Prefer one or two words on line 2.
- Keep both lines readable at a glance from across the room.
- Do not include secrets or detailed internal status.
