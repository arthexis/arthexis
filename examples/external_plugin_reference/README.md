# arthexis-plugin-sample

Reference plugin package showing the Arthexis external app contract.

## Enable in Arthexis

```python
ARTHEXIS_EXTERNAL_APPS = [
    "arthexis_plugin_sample.apps.ArthexisPluginSampleConfig",
]
```

Then run:

```bash
.venv/bin/pip install -e examples/external_plugin_reference
.venv/bin/python manage.py migrate
.venv/bin/python manage.py check --tag core
```
