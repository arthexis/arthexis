# Management command deprecation pattern

Arthexis no longer ships runtime shims for deprecated management commands.
When a command is absorbed into a newer surface, keep the compatibility guidance in documentation and release notes instead of importing transitional helpers at runtime.

## Minimal migration note template

Use a short mapping table in the built-in Arthexis changelog/report UI so operators can update automation scripts:

| Removed command | Replacement |
| --- | --- |
| `.venv/bin/python manage.py old_command [args...]` | `.venv/bin/python manage.py new_command [args...]` |

## Optional shell migration snippet

For one-time script updates, include a simple substitution example:

```bash
# GNU sed
sed -i \
  -e 's|.venv/bin/python manage.py old_command|.venv/bin/python manage.py new_command|g' \
  path/to/ops-script.sh

# BSD/macOS sed
sed -i '' \
  -e 's|.venv/bin/python manage.py old_command|.venv/bin/python manage.py new_command|g' \
  path/to/ops-script.sh
```
