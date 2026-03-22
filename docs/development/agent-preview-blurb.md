# Agent system-prompt preview blurb

Use this blurb in agent/system prompts to standardize UI/admin previews:

```text
When a UI/admin change is made, run the in-repo preview tool:
1) python manage.py migrate
2) python manage.py runserver 0.0.0.0:<port> --noreload
3) python manage.py preview --base-url http://127.0.0.1:<port> --path <admin_path> --output media/previews/<name>.png

Requirements:
- Preview creates and deletes a temporary admin account automatically unless `--no-login` is passed.
- Use default engine fallback order (chromium then firefox) unless troubleshooting.
- Report the generated image diagnostics line (brightness/white ratio/mostly_white).
- If `mostly_white=True`, treat the preview as suspicious and investigate before finalizing.
- `--full-page` is honored by Playwright; Selenium falls back to a viewport capture and emits a warning.
- Attach the screenshot artifact in markdown: ![preview](<artifact_path>)

For routine CI captures, prefer:
python manage.py preview --base-url http://127.0.0.1:<port> --ci-fast --wait-for-suite --path <path> --output-dir preview_output

The `--ci-fast` preset forces Playwright + Chromium, desktop viewport only, `domcontentloaded` readiness, and viewport-only screenshots so CI can fail fast without paying for multi-engine or multi-viewport retries.
```

The command prints a coarse image-health report (`white_ratio`, `mostly_white`) so agents can quickly detect blank/error-like captures.
