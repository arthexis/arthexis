---
name: ui-preview-capture
description: Capture deterministic Django UI/admin previews with the built-in preview command, including image diagnostics for mostly-white failure detection.
---

# UI Preview Capture

Use this skill for UI/admin changes that require visual confirmation.

## Required sequence

1. **Prepare server**
   - Run: `python manage.py migrate`
   - Start: `python manage.py runserver 0.0.0.0:<port> --noreload`

2. **Deterministic auth + capture**
   - Run:
     - `python manage.py preview --base-url http://127.0.0.1:<port> --path <admin_path> --output media/previews/<name>.png`
   - Default credentials are deterministic: `admin` / `admin123`.

3. **Diagnostics and validation**
   - Read the command output line with `white_ratio` and `mostly_white`.
   - Treat `mostly_white=True` as suspicious (possible error page/blank render) and investigate.

4. **Capture and report**
   - Attach screenshot with markdown: `![description](<artifact_path>)`.
   - Include command/test status in final report.

## Troubleshooting

- If unreachable: confirm runserver host is `0.0.0.0` and the same port is used.
- If login fails: rerun command with explicit `--username`/`--password` and verify admin auth backend state.
- If Chromium fails: keep default fallback order or force `--engine firefox` and report the failure text.
