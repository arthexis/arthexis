# Auto-Upgrade and Delegated Upgrade Flow

This document describes how nodes upgrade themselves through Celery, how the
delegated systemd unit is launched, and what to check if something fails.

## Prerequisites

- `./env-refresh.sh` has been run so `/usr/local/bin/watch-upgrade` exists and is
  executable.
- `.locks/service.lck` contains the managed service name (for example
  `arthexis`) so the watcher knows which unit to stop and restart.
- The service user can run `systemd-run` (with passwordless sudo when required).
- `upgrade.sh` remains executable in the project root.
- The suite-controlled `auto_upgrade_check` entry in `config/settings/celery.py`
  runs `apps.nodes.tasks.apply_upgrade_policies` once per day through the
  normal Celery beat service. This path does not depend on the home-level
  upgrade watchdog. `ARTHEXIS_UPGRADE_FREQ` can still affect policy recency
  checks inside the task, but it does not make the static beat entry fire more
  often than the configured daily cadence.
- Auto-upgrade channels are grouped into three tiers:
  - `stable`/`lts`: patch upgrades can proceed weekly, and minor/major upgrades
    are blocked by default policy gates.
  - `regular`/`normal`: patch and minor upgrades can proceed daily, and major
    upgrades can proceed weekly.
  - `latest`/`unstable`: tracks live `main` revisions daily instead of gating on
    release version bumps.
  - `custom`: uses the policy's configured branch, interval, live-branch toggle,
    and patch/minor/major bump gates.
  `ARTHEXIS_UPGRADE_FREQ` still overrides the check interval, but channel bump
  cadence gates whether a release upgrade may proceed.
- Release-channel upgrades are pinned to immutable release evidence. When the
  stable/LTS or regular/normal policy decides to upgrade to a `PackageRelease`,
  Celery passes the resolved release version, git revision, and `vVERSION` tag
  to `upgrade.sh`/`upgrade.bat`. The script verifies the target tag resolves to
  the expected revision and that the target commit's `VERSION` matches before it
  resets the worktree. This prevents post-release commits on `main` from being
  installed by a delayed release-channel auto-upgrade. The selected target is
  also recorded at `.locks/auto_upgrade_target.json` before execution.
- `latest`/`unstable` and custom live-branch policies intentionally remain
  branch-tracking paths. Use those channels when the desired behavior is "run
  whatever is current on the selected branch."
- Custom policies keep the existing `interval_minutes` frequency and add:
  `target_branch`, `include_live_branch`, `allow_patch_upgrades`,
  `allow_minor_upgrades`, and `allow_major_upgrades`. When live branch tracking
  is off, same-version revision changes are skipped. When it is on, same-version
  branch revisions can be applied with the latest/unstable upgrade path.
- Boot-time prestart checks (`scripts/boot-upgrade-prestart.sh`) keep a per-service
  recency lock at `.locks/<service>-boot-upgrade-last-check.lck` after a
  successful run. If the local revision is unchanged and the recency TTL has
  not expired, startup skips launching `upgrade.sh` to reduce repeated no-op
  checks on already-current nodes.

## How delegation works

1. Celery beat queues `apps.nodes.tasks.apply_upgrade_policies` daily. The task
   checks the local node's assigned upgrade policies and exits without action
   when the feature is disabled, no policy is assigned, or no policy is due.
2. Celery calls `scripts/delegated-upgrade.sh` when an update is required.
3. `scripts/delegated-upgrade.sh` launches a transient unit with `systemd-run`, setting:
   - `WorkingDirectory` to the project root so relative commands like
     `./upgrade.sh` resolve correctly.
   - `ARTHEXIS_BASE_DIR` and `ARTHEXIS_LOG_DIR` for the watcher.
   - `ARTHEXIS_PYTHON_BIN`, `VIRTUAL_ENV`, and `PATH` when the repository
     virtualenv exists, so predeploy checks use suite dependencies.
   - `StandardOutput`/`StandardError` appended to
     `logs/delegated-upgrade.log` for easy inspection.
4. The transient unit runs `/usr/local/bin/watch-upgrade`, which:
   - Stops the managed service stack before predeploy migration checks.
   - Executes `upgrade.sh` (default `--stable`; Celery can pass `--regular` or
     `--latest`).
   - Runs any executable local one-shot scripts in `.locks/post-upgrade.d/`
     after the refreshed code and migrations are in place and before service
     restart. Successful hooks are removed automatically; failed hooks remain
     in place and fail the upgrade. A later upgrade run retries them after it
     successfully resolves the local or remote upgrade target; if the remote
     fetch fails first, use `--local` or `--force` when an operator-approved
     local retry is appropriate.
   - Restarts the service stack and exits with the upgrade status.
5. Celery schedules a post-upgrade health check after the run to confirm HTTP
   200 responses. Failed health checks record the current revision in the
   auto-upgrade skip lock and require manual operator recovery; this path does
   not automatically revert the suite.

## Triggering an upgrade manually

Run one of the following from the project root:

```bash
./upgrade.sh --stable   # direct run, useful for local validation
./upgrade.sh --regular  # release upgrade path with regular/normal channel semantics
./upgrade.sh --regular --target-version 0.3.0 --target-revision <sha> --target-tag v0.3.0
./upgrade.sh --latest   # live-main path used by latest/unstable
./upgrade.sh --latest --branch lab/canary  # live custom branch path
./upgrade.sh --detached # launches the delegated watcher so the upgrade continues if the console disconnects
./scripts/delegated-upgrade.sh  # matches the automated delegated path
```

You can also request the Celery task:

```bash
# Django shell or worker context
from apps.core.tasks.auto_upgrade import check_github_updates
check_github_updates.delay()
```

## Requesting downstream upgrades

Upstream nodes can ask a downstream node to run its own local upgrade check with
a signed remote upgrade request. The request travels through the existing
NetMessage transport and downstream pull queue, but the downstream node remains
the authority: it accepts or rejects the request using its local policy and then
sends a signed response back to the requester.

Remote upgrade requests are controlled by the `remote-upgrade-requests` Suite
Feature, which is enabled by default as the global policy gate. Satellite nodes
accept requests by default. Other node roles can accept requests by setting:

```bash
ARTHEXIS_REMOTE_UPGRADE_REQUESTS=1
```

Any node can reject remote upgrade requests locally by setting:

```bash
ARTHEXIS_REMOTE_UPGRADE_REQUESTS=0
```

Optional allow-lists narrow what the downstream node will accept:

```bash
ARTHEXIS_REMOTE_UPGRADE_ALLOWED_CHANNELS=stable,regular
ARTHEXIS_REMOTE_UPGRADE_ALLOWED_UPSTREAMS=<uuid-or-hostname>
ARTHEXIS_REMOTE_UPGRADE_ALLOWED_UPSTREAM_ROLES=Terminal,Control
```

If `ARTHEXIS_REMOTE_UPGRADE_ALLOWED_CHANNELS` is unset, only `stable` and
`regular` are accepted. Requests from nodes not registered locally as
`Upstream` are rejected. Accepted requests call the same local upgrade-check
path as `python manage.py upgrade check`; request payloads are never executed as
shell commands.

Send a request from the upstream node:

```bash
python manage.py node upgrade-request --node <downstream-node> --channel stable --reason "maintenance window"
```

Inspect request and response state:

```bash
python manage.py node upgrade-request-status --limit 10
python manage.py node upgrade-request-status --request <request-uuid> --json
```

## Boot-time throttle knobs

Boot-time prestart upgrades still honor failure backoff in
`.locks/<service>-boot-upgrade-backoff-until.lck`, and now also support a
lightweight success recency throttle:

- `ARTHEXIS_BOOT_UPGRADE_CHECK_TTL_SECONDS` (default `300`) sets how long a
  successful boot-time check can be reused when the local revision is unchanged.
  Set to `0` to disable throttle reuse.
- `ARTHEXIS_BOOT_UPGRADE_FORCE_CHECK=1` bypasses recency throttle and forces a
  fresh `upgrade.sh` invocation (unless failure backoff is active).

The throttle is bypassed automatically when the revision changes or the TTL
expires.

## Observing progress

- Transient unit: `journalctl -u delegated-upgrade-<timestamp>.service`
- Delegated logs: `logs/delegated-upgrade.log`
- Watcher logs: `logs/watch-upgrade.log`
- Auto-upgrade timeline: `logs/auto-upgrade.log`

## Common issues

- **watch-upgrade missing**: rerun `./env-refresh.sh` to install the helper.
- **Permission denied on systemd-run**: grant the service user access or provide
  passwordless sudo for `systemd-run`.
- **Service not restarted**: ensure `.locks/service.lck` contains the correct
  unit name and that the unit exists in systemd.
