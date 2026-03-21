# App structure policy

This repository supports two app structure modes:

1. **Web-capable apps** (default) include `views.py`, `urls.py`, and `routes.py`.
2. **Backend-only/service apps** may intentionally omit `views.py`, `urls.py`, and `routes.py`.

## Marker convention for intentional omission

When an app intentionally omits web routing files, add this marker line in `manifest.py`:

```python
# APP_STRUCTURE: backend-only (intentionally omits views.py, urls.py, and routes.py)
```

This marker is the repository-wide convention that signals omission is intentional.

## Scaffold behavior

- `python manage.py create app <app_name>` generates the default web-capable scaffold.
- `python manage.py create app <app_name> --backend-only` generates a backend-only scaffold and writes the marker to `manifest.py`.
- `python manage.py create model <app_name> <model_name>` checks `manifest.py` for the marker:
  - if present, model scaffolding only updates model/admin code.
  - if absent, model scaffolding also updates view/url/route wiring.
