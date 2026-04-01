# Operational commands via `command.sh` / `command.bat`

`command.sh` (POSIX) and `command.bat` (Windows) now expose an explicit allowlist of operational one-word command entrypoints ("ops commands").

For advanced admin workflows and any non-allowlisted Django command, run `manage.py` directly.

## Usage

```bash
./command.sh list
./command.sh <command> [args...]
./manage.py <django-command> [args...]
```

## Supported operational commands

- `admin`
- `analytics`
- `apply_nginx_config`
- `apply_release_migrations`
- `availability`
- `benchmark`
- `browse`
- `camera_service`
- `changelog`
- `channels`
- `charger`
- `chargers`
- `configure_site`
- `coverage`
- `create`
- `create_docs_admin`
- `deploy`
- `dns_proxy`
- `email`
- `enable_local_https`
- `env`
- `estimate`
- `evergo`
- `feature`
- `features`
- `fixtures`
- `generate_certs`
- `generate_public_ocpp_sample_data`
- `godaddy`
- `good`
- `groups`
- `health`
- `https`
- `invite`
- `lcd`
- `leads`
- `lightsail`
- `message`
- `migrations`
- `nginx`
- `nginx_configure`
- `nginx_restart`
- `node`
- `notify`
- `ocpp`
- `odoo`
- `password`
- `preview`
- `prototype`
- `purge_net_messages`
- `purge_nodes`
- `reconcile_node_features_services`
- `record`
- `redis`
- `refresh_node_features`
- `register_site_apps`
- `release`
- `repo`
- `reset_ocpp_migrations`
- `rfid`
- `run_release_data_transforms`
- `run_scheduled_sql_reports`
- `runftpserver`
- `runserver`
- `shortcut_listener`
- `show_rfid_history`
- `simulator`
- `smb`
- `startup`
- `summary`
- `sync_registered_widgets`
- `sync_specials`
- `test`
- `test_login`
- `track_cp_forward`
- `upgrade`
- `uptime`
- `utils`
- `verify_certs`
- `video`
- `view_errors`

## Notes for AGENTS and operators

When an operation explicitly asks for an **ops command**, use `command.sh` / `command.bat` with one of the allowlisted names above.

For everything else (including Django built-ins like `makemigrations`, `shell`, and direct test targeting), use `manage.py` directly.

### `lightsail` command quickstart

Use Arthexis lightsail setup to create and register a new AWS Lightsail target in one CLI flow:

```bash
./command.sh lightsail \
  --credentials <aws-credential-id-or-name> \
  --region us-east-1 \
  --instance-name ops-node-1 \
  --blueprint-id debian_12 \
  --bundle-id small_3_0
```

Then inspect the configured deployment inventory:

```bash
./command.sh deploy
```

If saved AWS credentials are stale/invalid, refresh them inline before setup/fetch:

```bash
./command.sh lightsail \
  --credentials <aws-credential-id-or-name> \
  --refresh-credentials \
  --access-key-id <new-access-key-id> \
  --secret-access-key <new-secret-access-key> \
  --region us-east-1 \
  --instance-name ops-node-1 \
  --skip-create
```
