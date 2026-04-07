# External Django app plugin contract

Arthexis can be extended by installing standalone Django apps and enabling them through a settings contract.

## Settings contract

Declare external plugin `AppConfig` dotted paths in `ARTHEXIS_EXTERNAL_APPS`:

```python
# config/settings/apps.py
ARTHEXIS_EXTERNAL_APPS = [
    "arthexis_plugin_sample.apps.ArthexisPluginSampleConfig",
]
```

Arthexis app settings append `ARTHEXIS_EXTERNAL_APPS` to `INSTALLED_APPS` during startup, so external plugins boot with the rest of the suite.

When external plugins are configured, Arthexis also provisions one SQLite
database alias per plugin under `work/dbs/` using the naming pattern
`external_<module_last_segment>.sqlite3`, where `<module_last_segment>` is
derived from the plugin module path. For
`arthexis_plugin_sample.apps.ArthexisPluginSampleConfig`, the alias and file
name are `external_arthexis_plugin_sample` and
`external_arthexis_plugin_sample.sqlite3`.

## Compatibility contract

Each plugin `AppConfig` must declare:

- `arthexis_compatibility` as a [PEP 440 version specifier](https://packaging.pypa.io/en/latest/specifiers.html), for example `">=0.2,<0.3"`.

During startup checks, Arthexis validates:

1. The configured plugin `AppConfig` dotted path is importable.
2. `arthexis_compatibility` exists.
3. The compatibility range is a valid specifier.
4. The running Arthexis version is inside that range.

If any check fails, Arthexis raises a startup configuration error through the core checks framework.

## Required plugin structure

Each external plugin repository should include:

- `apps.py` with an `AppConfig` class and `arthexis_compatibility`.
- `admin.py` for model registration in Django admin when the plugin exposes models.
- `migrations/` for database schema history when models are present.
- Optional Django commands in `management/commands/`.

Recommended minimal package tree:

```text
arthexis_plugin_sample/
  __init__.py
  admin.py
  apps.py
  migrations/
    __init__.py
    0001_initial.py
  models.py
  management/
    __init__.py
    commands/
      __init__.py
      health_ping.py
```

## Reference plugin package

A reference standalone package is included in `examples/external_plugin_reference/` as a template you can publish in a separate repository.

### Install and enable

1. Publish/install the plugin package into the same environment as Arthexis (wheel, editable install, or private index).
2. Add the plugin `AppConfig` path to `ARTHEXIS_EXTERNAL_APPS`.
3. Run migrations:

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py migrate --database external_arthexis_plugin_sample
```

4. Validate startup checks:

```bash
.venv/bin/python manage.py check --tag core
```

This keeps integrations inside the Arthexis suite model by extending the platform through native Django apps, migrations, admin, and commands.
