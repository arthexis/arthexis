# Logging domain
This domain centralizes how Arthexis selects log destinations and routes output from Django, Celery, and supporting scripts.
## Log directory selection
* `select_log_dir` chooses the first writable candidate from `ARTHEXIS_LOG_DIR`, the repository `logs/` folder, user state directories, or system fallbacks (including `/var/log/arthexis` when running as root). It also exports `ARTHEXIS_LOG_DIR` for child processes so Python and shell tooling share the same location.
* Django loads `LOG_DIR` via `build_logging_settings`, which calls `select_log_dir` and exposes the resolved path alongside the full `LOGGING` dict used by the app and Celery workers.
## Standard log files
* **Active app log**: `ActiveAppFileHandler` writes to `<active-app>.log` (defaulting to the host name) so deployments with multiple apps or threads can separate output. In test runs the file becomes `tests.log`. This handler is attached to the root logger for general application traffic.
* **Error log**: `error.log` (or `tests-error.log` in tests) captures records at `ERROR` level and above through the dedicated `ErrorFileHandler`, which also hangs off the root logger.
* **Celery log**: `celery.log` (or `tests-celery.log`) receives Celery and worker trace output via `CeleryFileHandler`, keeping worker chatter separate from the shared error log. Celery-specific loggers are wired to this handler and the error handler without propagation to other handlers.
## Dependent systems and auxiliary outputs
* **Celery runtime**: Celery workers reuse the `standard` formatter from the logging config so worker log lines match Django output while respecting the `celery.log` routing described above.
* **OCPP session logging**: The OCPP store selects the same `LOG_DIR`, then writes per-charger and simulator files (e.g., `charger.<id>.log`) and session captures under `logs/sessions/` using that shared path.
* **Release publishing**: Headless release workflows and the `clean_release_logs` management command rely on `settings.LOG_DIR` for publish logs named `pr.<package>.v<version>.log`, and they fall back to `select_log_dir` when the preferred path is not writable.
* **Node utilities**: Screenshot and audio captures land in `logs/screenshots/` and `logs/audio/`, and node registration helpers create dedicated `register_visitor_node.log` and `register_local_node.log` files alongside the main logs.
* **Shell automation**: Startup, upgrade, and service scripts source `scripts/helpers/logging.sh` to mirror `select_log_dir`'s candidate search (including honoring `ARTHEXIS_LOG_DIR`) so their `.log` outputs live beside the Django logs.
