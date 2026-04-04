---
name: lcd-review-notify
description: Emit a review-ready notification after automated or manual coding tasks by calling the repo review notifier, using the LCD when available and a transparent fallback notification path when not.
---

# LCD Review Notify

Use this skill when a task leaves local code or generated artifacts ready for human review.

## Required sequence

1. From the repo root, run `./scripts/review-notify.sh --actor Codex` before the final response when the task leaves reviewable changes.
2. If a short custom hint would help the reviewer, add `--summary "<short text>"`.
3. If the command reports `Skipped review notification`, do not force it unless the user explicitly asked for a notification without file changes.
4. Mention in the final response whether the notification went to the LCD or the fallback notification path.

## Notes

- The notifier is transparent on nodes without `lcd-screen`; it reports fallback usage instead of failing.
- The default expiry is 30 minutes. Override with `--expires-in <seconds>` when needed.
- Manual use is the same, for example `./scripts/review-notify.sh --actor Manual`.
