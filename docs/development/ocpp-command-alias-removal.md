# OCPP legacy command alias removal

The legacy single-purpose command aliases have been retired. Operators and automation scripts must invoke the canonical unified command surface:

- `manage.py coverage_ocpp16` → `manage.py ocpp coverage --version 1.6J`
- `manage.py coverage_ocpp201` → `manage.py ocpp coverage --version 2.0.1`
- `manage.py coverage_ocpp21` → `manage.py ocpp coverage --version 2.1`
- `manage.py import_transactions <input.json>` → `manage.py ocpp transactions import <input.json>`
- `manage.py export_transactions <output.json> [flags]` → `manage.py ocpp transactions export <output.json> [flags]`
- `manage.py ocpp_replay <extract.json>` → `manage.py ocpp trace replay <extract.json>`

## Migration guidance for scripts

1. Replace every deprecated command alias invocation with the canonical `manage.py ocpp ...` command in cron jobs, systemd units, and deployment hooks.
2. Keep argument payloads the same where possible; only the command prefix has changed.
3. Validate automation by running each updated command once in staging and checking exit code plus generated artifacts.
4. Update runbooks and operational docs to avoid reintroducing removed aliases.

For full command syntax and examples, use `manage.py ocpp --help` and the OCPP user manual.
