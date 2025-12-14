# Logging domain

This domain centralizes how Arthexis selects log destinations and routes output from Django, Celery, and supporting scripts.

## Log directory selection
* `select_log_dir` chooses the first writable candidate from `ARTHEXIS_LOG_DIR`, the repository `logs/` folder, user state directories, or system fallbacks (including `/var/log/arthexis` when running as root). It also exports `ARTHEXIS_LOG_DIR` for child processes so Python and shell tooling share the same location.【F:apps/loggers/paths.py†L27-L114】
* Django loads `LOG_DIR` via `build_logging_settings`, which calls `select_log_dir` and exposes the resolved path alongside the full `LOGGING` dict used by the app and Celery workers.【F:apps/loggers/config.py†L34-L108】【F:config/settings.py†L785-L806】

## Standard log files
* **Active app log**: `ActiveAppFileHandler` writes to `<active-app>.log` (defaulting to the host name) so deployments with multiple apps or threads can separate output. In test runs the file becomes `tests.log`. This handler is attached to the root logger for general application traffic.【F:apps/loggers/config.py†L51-L88】【F:apps/loggers/handlers.py†L16-L44】
* **Error log**: `error.log` (or `tests-error.log` in tests) captures records at `ERROR` level and above through the dedicated `ErrorFileHandler`, which also hangs off the root logger.【F:apps/loggers/config.py†L60-L87】【F:apps/loggers/handlers.py†L46-L55】
* **Celery log**: `celery.log` (or `tests-celery.log`) receives Celery and worker trace output via `CeleryFileHandler`, keeping worker chatter separate from the shared error log. Celery-specific loggers are wired to this handler and the error handler without propagation to other handlers.【F:apps/loggers/config.py†L69-L105】【F:apps/loggers/handlers.py†L57-L65】

## Dependent systems and auxiliary outputs
* **Celery runtime**: Celery workers reuse the `standard` formatter from the logging config so worker log lines match Django output while respecting the `celery.log` routing described above.【F:config/settings.py†L785-L806】
* **OCPP session logging**: The OCPP store selects the same `LOG_DIR`, then writes per-charger and simulator files (e.g., `charger.<id>.log`) and session captures under `logs/sessions/` using that shared path.【F:apps/ocpp/store.py†L64-L80】【F:apps/ocpp/store.py†L704-L745】
* **Release publishing**: Headless release workflows and the `clean_release_logs` management command rely on `settings.LOG_DIR` for publish logs named `pr.<package>.v<version>.log`, and they fall back to `select_log_dir` when the preferred path is not writable.【F:apps/core/views.py†L418-L472】【F:apps/release/release_workflow.py†L81-L128】【F:apps/release/management/commands/clean_release_logs.py†L13-L77】
* **Node utilities**: Screenshot and audio captures land in `logs/screenshots/` and `logs/audio/`, and node registration helpers create dedicated `register_visitor_node.log` and `register_local_node.log` files alongside the main logs.【F:apps/nodes/utils.py†L22-L94】【F:apps/nodes/logging.py†L15-L56】
* **Shell automation**: Startup, upgrade, and service scripts source `scripts/helpers/logging.sh` to mirror `select_log_dir`'s candidate search (including honoring `ARTHEXIS_LOG_DIR`) so their `.log` outputs live beside the Django logs.【F:scripts/helpers/logging.sh†L22-L105】
