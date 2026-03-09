# Agent system-prompt preview blurb

Use this blurb in agent/system prompts to standardize UI/admin previews:

```text
When a UI/admin change is made, run the in-repo preview tool:
1) python manage.py migrate
2) python manage.py runserver 0.0.0.0:<port> --noreload
3) python manage.py preview --base-url http://127.0.0.1:<port> --path <admin_path> --output media/previews/<name>.png

Requirements:
- Use deterministic admin credentials (`admin` / `admin123`) unless explicitly overridden.
- Use default engine fallback order (chromium then firefox) unless troubleshooting.
- Report the generated image diagnostics line (brightness/white ratio/mostly_white).
- If `mostly_white=True`, treat the preview as suspicious and investigate before finalizing.
- Attach the screenshot artifact in markdown: ![preview](<artifact_path>)
```

The command prints a coarse image-health report (`white_ratio`, `mostly_white`) so agents can quickly detect blank/error-like captures.
