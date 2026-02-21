---
name: ui-preview-capture
description: Capture deterministic Django UI/admin previews by running migrate + runserver on explicit host/port, forwarding the same port to Playwright, logging in with stable admin credentials, trying Chromium first, then Firefox fallback, and attaching screenshot artifacts.
---

# UI Preview Capture

Use this skill for UI/admin changes that require visual confirmation.

## Required sequence

1. **Prepare server**
   - Run: `python manage.py migrate`
   - Start: `python manage.py runserver 0.0.0.0:<port> --noreload`

2. **Deterministic auth**
   - Ensure an admin account exists (example: `admin` / `admin123`).
   - Use the same credentials during browser automation.

3. **Browser automation**
   - Use Playwright with `ports_to_forward=[<port>]`.
   - Verify server reachability before login (e.g., wait for URL + status check behavior).
   - Attempt **Chromium first**.
   - If Chromium fails, retry with **Firefox**.
   - If both fail, report exact failure text and attempted steps.

4. **Capture and report**
   - Save screenshot to a relative artifact path.
   - Include screenshot in markdown: `![description](<artifact_path>)`.
   - Include command/test status in final report.

## Minimal Playwright expectations

- Navigate to login page on forwarded localhost port.
- Submit deterministic admin credentials.
- Navigate to changed page.
- Wait for stable UI state.
- Capture screenshot artifact.

## Troubleshooting

- If page unreachable: confirm runserver host is `0.0.0.0` and port matches forwarded port.
- If login fails: recreate/reset deterministic admin credential and retry.
- If Chromium-only issue: document error, retry once with Firefox.
