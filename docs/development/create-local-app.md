# create command (`create_local_app` compatibility)

Use the one-word `create` management command to scaffold either:

- new local apps (`create app ...`)
- new models inside existing local apps (`create model ...`)

`create_local_app` is now a compatibility alias that delegates to `create app`.

## Usage

```bash
python manage.py create app <app_name>
python manage.py create model <app_name> <ModelName|model_name>
```

Compatibility alias:

```bash
python manage.py create_local_app <app_name>
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

`manifest.py` includes:

```python
DJANGO_APPS = [
    "apps.<name>",
]
```

## Model scaffold wiring

`create model <app> <model>` appends a starter model + admin registration + list/detail views + URL patterns in the target app and ensures `routes.py` includes the app URL mount.

## After generating

1. Run:
   - `python manage.py makemigrations <app_name>`
   - `python manage.py migrate`
2. Add templates for generated views under `apps/<app_name>/templates/<app_name>/`.
3. Extend fields/admin/views/tests for your domain.
