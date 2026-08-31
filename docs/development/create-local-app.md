# create command (`create_local_app` compatibility)

Use the one-word `create` management command to scaffold either:

- new local apps (`create app ...`)
- new models inside existing local apps (`create model ...`)

`create_local_app` is now a compatibility alias that delegates to `create app`.

## Usage

```bash
.venv/bin/python manage.py create app <app_name>
.venv/bin/python manage.py create app <app_name> --backend-only
.venv/bin/python manage.py create model <app_name> <ModelName|model_name>
```

Compatibility alias:

```bash
.venv/bin/python manage.py create_local_app <app_name>
.venv/bin/python manage.py create_local_app <app_name> --backend-only
```

## App scaffold policy

See [app structure policy](./app-structure-policy.md).

- Default app scaffold is **web-capable** and includes `views.py`, `urls.py`, and `routes.py`.
- Backend-only/service apps may omit those files by using `--backend-only`.
- Backend-only app scaffolds include this marker in `manifest.py`:

```python
# APP_STRUCTURE: backend-only (intentionally omits views.py, urls.py, and routes.py)
```

## App scaffold output

`create app <name>` generates:

- `apps/<name>/__init__.py`
- `apps/<name>/apps.py`
- `apps/<name>/models.py`
- `apps/<name>/admin.py`
- `apps/<name>/views.py`
- `apps/<name>/urls.py`
- `apps/<name>/manifest.py`
- `apps/<name>/routes.py`
- `apps/<name>/migrations/__init__.py`
- `apps/<name>/tests/test_<name>_smoke.py`

`create app <name> --backend-only` omits:

- `apps/<name>/views.py`
- `apps/<name>/urls.py`
- `apps/<name>/routes.py`

`manifest.py` includes:

```python
DJANGO_APPS = [
    "apps.<name>",
]
```

## Model scaffold wiring

`create model <app> <model>` appends a starter model + admin registration.

For web-capable apps, it also appends list/detail views + URL patterns and ensures `routes.py` includes the app URL mount.

For backend-only apps (marker present in `manifest.py`), it skips view/URL/route wiring.

## After generating

1. Run:
   - `.venv/bin/python manage.py makemigrations <app_name>`
   - `.venv/bin/python manage.py migrate`
2. Add templates for generated views under `apps/<app_name>/templates/<app_name>/` when using a web-capable scaffold.
3. Extend fields/admin/views/tests for your domain.
