from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
import urllib.error
import urllib.request

import requests

from celery import shared_task
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from apps.core.auto_upgrade import (
    AUTO_UPGRADE_FALLBACK_INTERVAL,
    AUTO_UPGRADE_INTERVAL_MINUTES,
    DEFAULT_AUTO_UPGRADE_MODE,
    append_auto_upgrade_log,
    auto_upgrade_base_dir,
    shorten_auto_upgrade_failure,
)
from apps.core.notifications import LcdChannel
from apps.release import release_workflow

from ..system_ops import _ensure_runtime_services
from ..utils import _get_package_release_model
from .canaries import _canary_gate
from .locks import (
    _add_skipped_revision,
    _auto_upgrade_ran_recently,
    _read_auto_upgrade_failure_count,
    _record_auto_upgrade_failure as _record_auto_upgrade_failure_base,
    _record_auto_upgrade_timestamp,
    _reset_auto_upgrade_failure_count,
    _reset_network_failure_count,
    _load_skipped_revisions,
)
from .network import _handle_network_failure_if_applicable, _is_network_failure
from .runner import (
    _delegate_upgrade_via_script,
    _resolve_service_url,
    _run_upgrade_command,
    _upgrade_command_args,
)
from .scheduling import (
    _apply_stable_schedule_guard,
    _resolve_auto_upgrade_interval_minutes,
)


AUTO_UPGRADE_HEALTH_DELAY_SECONDS = 300
AUTO_UPGRADE_LCD_CHANNEL_TYPE = LcdChannel.HIGH.value
AUTO_UPGRADE_LCD_CHANNEL_NUM = 1
NON_TERMINAL_ROLES = {"Control", "Constellation", "Watchtower"}

SEVERITY_NORMAL = "normal"
SEVERITY_LOW = "low"
SEVERITY_CRITICAL = "critical"

model = _get_package_release_model()
if model is not None:  # pragma: no branch - runtime constant setup
    SEVERITY_NORMAL = model.Severity.NORMAL
    SEVERITY_LOW = model.Severity.LOW
    SEVERITY_CRITICAL = model.Severity.CRITICAL


logger = logging.getLogger(__name__)


def _resolve_release_severity(version: str | None) -> str:
    try:
        return release_workflow.resolve_release_severity(version)
    except Exception:  # pragma: no cover - protective fallback
        logger.exception("Failed to resolve release severity")
        return SEVERITY_NORMAL


def _project_base_dir() -> Path:
    """Return the filesystem base directory for runtime operations."""

    return auto_upgrade_base_dir()


def _normalize_channel_mode(value: str | None) -> str | None:
    """Return a lowercased, whitespace-trimmed channel value."""

    if value is None:
        return None

    normalized = value.strip().lower()
    return normalized or None


def _latest_release() -> tuple[str | None, str | None, str | None]:
    """Return the latest release version, revision, and PyPI URL when available."""

    model = _get_package_release_model()
    if model is None:
        return None, None, None

    try:
        release = model.latest()
    except DatabaseError:  # pragma: no cover - depends on DB availability
        return None, None, None
    except Exception:  # pragma: no cover - defensive catch-all
        return None, None, None

    if not release:
        return None, None, None

    version = getattr(release, "version", None)
    revision = getattr(release, "revision", None)
    pypi_url = getattr(release, "pypi_url", None)
    return version, revision, pypi_url


def _read_local_version(base_dir: Path) -> str | None:
    """Return the local VERSION file contents when readable."""

    version_path = base_dir / "VERSION"
    if not version_path.exists():
        return None
    try:
        return version_path.read_text().strip()
    except OSError:  # pragma: no cover - filesystem error
        return None


def _read_remote_version(base_dir: Path, branch: str) -> str | None:
    """Return the VERSION file from ``origin/<branch>`` when available."""

    try:
        return (
            subprocess.check_output(
                [
                    "git",
                    "show",
                    f"origin/{branch}:VERSION",
                ],
                cwd=base_dir,
                stderr=subprocess.STDOUT,
                text=True,
            )
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover - git failure
        return None


def _parse_github_slug(remote_url: str) -> str | None:
    """Normalize the GitHub repository slug from the ``origin`` remote URL."""

    cleaned = remote_url.strip()
    if not cleaned:
        return None

    if "github.com" not in cleaned:
        return None

    if cleaned.startswith("git@github.com:"):
        slug = cleaned.split(":", 1)[-1]
    else:
        parts = cleaned.split("github.com/", 1)
        slug = parts[-1] if len(parts) > 1 else ""

    if slug.endswith(".git"):
        slug = slug[:-4]

    return slug or None


def _resolve_github_slug(base_dir: Path) -> str | None:
    """Return the ``owner/repo`` slug for the ``origin`` remote when available."""

    try:
        remote_url = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=base_dir,
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return None

    return _parse_github_slug(remote_url)


def _fetch_ci_workflow_status(repo_slug: str, branch: str, workflow: str = "ci.yml") -> str | None:
    """Return the latest completed CI workflow status for ``branch`` when available."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "arthexis-auto-upgrade",
    }

    token = os.environ.get("GITHUB_TOKEN", "")
    if isinstance(token, str):
        cleaned = token.strip()
        if cleaned:
            headers["Authorization"] = f"token {cleaned}"

    url = f"https://api.github.com/repos/{repo_slug}/actions/workflows/{workflow}/runs"

    response = None
    try:
        response = requests.get(
            url,
            headers=headers,
            params={"branch": branch, "status": "completed", "per_page": 1},
            timeout=10,
        )
    except requests.RequestException:
        logger.warning("Failed to query CI workflow status for %s", repo_slug, exc_info=True)
        return None

    try:
        if response is None or response.status_code != 200:
            logger.warning(
                "CI workflow status request for %s returned %s",
                repo_slug,
                getattr(response, "status_code", "<unknown>"),
            )
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        runs = payload.get("workflow_runs")
        if not isinstance(runs, list) or not runs:
            return None

        conclusion = runs[0].get("conclusion")
        return conclusion.lower() if isinstance(conclusion, str) else None
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()


def _fetch_ci_status(repo_slug: str, revision: str) -> str | None:
    """Return the combined CI status for ``revision`` when available."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "arthexis-auto-upgrade",
    }

    token = os.environ.get("GITHUB_TOKEN", "")
    if isinstance(token, str):
        cleaned = token.strip()
        if cleaned:
            headers["Authorization"] = f"token {cleaned}"

    url = f"https://api.github.com/repos/{repo_slug}/commits/{revision}/status"

    response = None
    try:
        response = requests.get(url, headers=headers, timeout=10)
    except requests.RequestException:
        logger.warning("Failed to query CI status for %s", repo_slug, exc_info=True)
        return None

    try:
        if response is None or response.status_code != 200:
            logger.warning(
                "CI status request for %s returned %s", repo_slug, getattr(response, "status_code", "<unknown>")
            )
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        state = payload.get("state")
        return state.lower() if isinstance(state, str) else None
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()


def _ci_status_for_revision(base_dir: Path, revision: str, branch: str = "main") -> str | None:
    """Return the CI status value aligned with the main branch badge when available."""

    repo_slug = _resolve_github_slug(base_dir)
    if not repo_slug:
        return None

    branch_status = _fetch_ci_workflow_status(repo_slug, branch or "main")
    if branch_status:
        return branch_status

    return _fetch_ci_status(repo_slug, revision)


def _parse_major_minor(version: str) -> tuple[int, int] | None:
    match = re.match(r"^\s*(\d+)\.(\d+)", version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _shares_stable_series(local: str, remote: str) -> bool:
    local_parts = _parse_major_minor(local)
    remote_parts = _parse_major_minor(remote)
    if not local_parts or not remote_parts:
        return False
    return local_parts == remote_parts


@dataclass
class AutoUpgradeOperations:
    git_fetch: Callable[[Path, str], None]
    resolve_remote_revision: Callable[[Path, str], str]
    ensure_runtime_services: Callable[
        [Path, bool, bool, Callable[[Path, str], Any]],
        bool,
    ]
    delegate_upgrade: Callable[[Path, list[str]], str | None]
    run_upgrade_command: Callable[[Path, list[str]], tuple[str | None, bool]]


@dataclass
class AutoUpgradeMode:
    mode: str
    admin_override: bool
    override_log: str | None
    mode_file_exists: bool
    mode_file_physical: bool
    interval_minutes: int
    requires_canaries: bool
    requires_pypi: bool
    policy_id: int | None = None
    policy_name: str | None = None
    skip_recency_check: bool = False


@dataclass
class AutoUpgradeState:
    reset_network_failures: bool = True
    failure_recorded: bool = False


@dataclass
class AutoUpgradeRepositoryState:
    remote_revision: str
    release_version: str | None
    release_revision: str | None
    release_pypi_url: str | None
    remote_version: str | None
    local_version: str | None
    local_revision: str
    severity: str


def _default_auto_upgrade_operations() -> AutoUpgradeOperations:
    return AutoUpgradeOperations(
        git_fetch=_git_fetch,
        resolve_remote_revision=_git_remote_revision,
        ensure_runtime_services=_ensure_runtime_services,
        delegate_upgrade=_delegate_upgrade_via_script,
        run_upgrade_command=_run_upgrade_command,
    )


def _ensure_git_safe_directory(base_dir: Path) -> None:
    if shutil.which("git") is None:
        return

    base_dir_str = str(base_dir)
    check_result = subprocess.run(
        ["git", "config", "--global", "--get-all", "safe.directory", base_dir_str],
        cwd=base_dir,
        capture_output=True,
        text=True,
    )
    if check_result.returncode == 0:
        return

    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", base_dir_str],
        cwd=base_dir,
        capture_output=True,
        text=True,
        check=True,
    )


def _auto_upgrade_enabled(base_dir: Path) -> bool:
    try:  # pragma: no cover - optional dependency
        from apps.nodes.models import Node
    except Exception:
        return (base_dir / ".locks" / "auto_upgrade.lck").exists()

    try:
        local = Node.get_local()
    except (DatabaseError, Node.DoesNotExist):
        return (base_dir / ".locks" / "auto_upgrade.lck").exists()

    if not local:
        return (base_dir / ".locks" / "auto_upgrade.lck").exists()

    try:
        return local.upgrade_policies.exists()
    except DatabaseError:
        return (base_dir / ".locks" / "auto_upgrade.lck").exists()


def _is_non_terminal_role(role_name: str) -> bool:
    return role_name in NON_TERMINAL_ROLES


def _git_repo_dirty(base_dir: Path) -> bool:
    _ensure_git_safe_directory(base_dir)
    status = subprocess.check_output(
        ["git", "status", "--porcelain"],
        cwd=base_dir,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return bool(status.strip())


def _discard_local_git_changes(base_dir: Path) -> None:
    subprocess.run(
        ["git", "reset", "--hard", "HEAD"],
        cwd=base_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "clean", "-fd", "-e", "data/"],
        cwd=base_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def _prepare_manual_auto_upgrade_repo(base_dir: Path) -> None:
    role_name = getattr(settings, "NODE_ROLE", "Terminal")
    if not (_auto_upgrade_enabled(base_dir) or _is_non_terminal_role(role_name)):
        return

    try:
        repo_dirty = _git_repo_dirty(base_dir)
    except subprocess.CalledProcessError as exc:
        append_auto_upgrade_log(
            base_dir,
            f"Unable to read git status before manual upgrade check: {exc}",
        )
        raise

    if not repo_dirty:
        return

    append_auto_upgrade_log(
        base_dir,
        "Manual upgrade check requested; discarding local changes before checking for updates.",
    )

    try:
        _discard_local_git_changes(base_dir)
    except subprocess.CalledProcessError as exc:
        error_output = (exc.stderr or exc.stdout or "").strip()
        error_message = (
            f"Unable to discard local changes for manual upgrade check "
            f"(exit code {exc.returncode})"
        )
        if error_output:
            error_message = f"{error_message}: {error_output}"
        append_auto_upgrade_log(base_dir, error_message)
        raise


def _git_fetch(base_dir: Path, branch: str) -> None:
    _ensure_git_safe_directory(base_dir)
    subprocess.run(
        ["git", "fetch", "origin", branch],
        cwd=base_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_remote_revision(base_dir: Path, branch: str) -> str:
    _ensure_git_safe_directory(base_dir)
    return subprocess.check_output(
        ["git", "rev-parse", f"origin/{branch}"],
        cwd=base_dir,
        stderr=subprocess.STDOUT,
        text=True,
    ).strip()


def _load_upgrade_policy(policy_id: int | None):
    if policy_id is None:
        return None
    try:  # pragma: no cover - optional dependency
        from apps.nodes.models import UpgradePolicy
    except Exception:
        return None

    try:
        return UpgradePolicy.objects.filter(pk=policy_id).first()
    except DatabaseError:
        return None


def _resolve_auto_upgrade_mode(
    base_dir: Path,
    channel_override: str | None,
    *,
    policy=None,
) -> AutoUpgradeMode:
    mode_file = base_dir / ".locks" / "auto_upgrade.lck"
    mode_file_exists = mode_file.exists()
    mode = DEFAULT_AUTO_UPGRADE_MODE
    mode_file_physical = mode_file.is_file()
    requires_canaries = False
    requires_pypi = False
    policy_id = None
    policy_name = None
    skip_recency_check = False

    if policy is not None:
        mode = (getattr(policy, "channel", "") or DEFAULT_AUTO_UPGRADE_MODE).lower()
        interval_minutes = int(getattr(policy, "interval_minutes", 0) or 0)
        if interval_minutes <= 0:
            interval_minutes = AUTO_UPGRADE_FALLBACK_INTERVAL
        requires_canaries = bool(getattr(policy, "requires_canaries", False))
        requires_pypi = bool(getattr(policy, "requires_pypi_packages", False))
        policy_id = getattr(policy, "pk", None)
        policy_name = getattr(policy, "name", None)
        mode_file_exists = False
        mode_file_physical = False
        skip_recency_check = True

        mode = {
            "latest": "unstable",
            "unstable": "unstable",
            "stable": "stable",
            "normal": "stable",
            "regular": "stable",
        }.get(mode, mode)

        return AutoUpgradeMode(
            mode=mode,
            admin_override=False,
            override_log=None,
            mode_file_exists=mode_file_exists,
            mode_file_physical=mode_file_physical,
            interval_minutes=interval_minutes,
            requires_canaries=requires_canaries,
            requires_pypi=requires_pypi,
            policy_id=policy_id,
            policy_name=policy_name,
            skip_recency_check=skip_recency_check,
        )

    try:
        raw_mode = mode_file.read_text() if mode_file_exists else ""
    except (OSError, UnicodeDecodeError):
        logger.warning("Failed to read auto-upgrade mode lockfile", exc_info=True)
    else:
        cleaned_mode = _normalize_channel_mode(raw_mode)
        if cleaned_mode:
            mode = cleaned_mode

    override_log: str | None = None
    override_mode = _normalize_channel_mode(channel_override)
    if override_mode:
        if override_mode in {"latest", "unstable"}:
            mode = "unstable"
            override_log = "latest"
        elif override_mode in {"stable", "normal", "regular", "version"}:
            mode = "stable"
            if override_mode == "stable":
                override_log = "stable"

    mode = {
        "latest": "unstable",
        "unstable": "unstable",
        "version": "stable",
        "stable": "stable",
        "normal": "stable",
        "regular": "stable",
    }.get(mode, mode)

    interval_minutes = _resolve_auto_upgrade_interval_minutes(mode)

    return AutoUpgradeMode(
        mode=mode,
        admin_override=channel_override is not None,
        override_log=override_log,
        mode_file_exists=mode_file_exists,
        mode_file_physical=mode_file_physical,
        interval_minutes=interval_minutes,
        requires_canaries=requires_canaries,
        requires_pypi=requires_pypi,
        policy_id=policy_id,
        policy_name=policy_name,
        skip_recency_check=skip_recency_check,
    )


def _log_auto_upgrade_trigger(base_dir: Path) -> Path:
    return append_auto_upgrade_log(base_dir, "check_github_updates triggered")


def _record_auto_upgrade_failure(base_dir: Path, reason: str) -> int:
    normalized_reason = shorten_auto_upgrade_failure(reason)
    count = _record_auto_upgrade_failure_base(base_dir, normalized_reason)
    _send_auto_upgrade_failure_message(base_dir, normalized_reason, count)
    return count


def _broadcast_upgrade_start_message(
    local_revision: str | None, remote_revision: str | None
) -> None:
    from apps.nodes.models import NetMessage

    subject = _resolve_upgrade_subject()
    previous_revision = _short_revision(local_revision)
    next_revision = _short_revision(remote_revision)
    body = f"{previous_revision} - {next_revision}"

    try:
        NetMessage.broadcast(
            subject=subject,
            body=body,
            lcd_channel_type=AUTO_UPGRADE_LCD_CHANNEL_TYPE,
            lcd_channel_num=AUTO_UPGRADE_LCD_CHANNEL_NUM,
        )
    except Exception:
        logger.warning(
            "Failed to broadcast auto-upgrade start Net Message", exc_info=True
        )


def _resolve_upgrade_subject() -> str:
    from apps.nodes.models import Node

    fallback_name = socket.gethostname() or "node"

    try:
        node = Node.get_local()
    except Exception:
        logger.warning(
            "Auto-upgrade notification node lookup failed", exc_info=True
        )
        node_name = fallback_name
    else:
        node_name = getattr(node, "hostname", None) or fallback_name

    return f"Upgrade {node_name}".strip()


def _short_revision(revision: str | None) -> str:
    if not revision:
        return "-"
    trimmed = str(revision)
    return trimmed[-6:] if len(trimmed) > 6 else trimmed


def _send_auto_upgrade_failure_message(base_dir: Path, reason: str, failure_count: int) -> None:
    from apps.nodes.models import NetMessage, Node

    try:
        node = Node.get_local()
    except Exception:
        logger.warning(
            "Auto-upgrade failure Net Message skipped: local node unavailable",
            exc_info=True,
        )
        return

    node_name = getattr(node, "hostname", None) or socket.gethostname() or "node"
    timestamp = timezone.localtime(timezone.now())
    formatted_time = timestamp.strftime("%H:%M")
    subject = f"{node_name} {formatted_time}"
    body = f"{reason} x{failure_count}"

    try:
        NetMessage.broadcast(
            subject=subject,
            body=body,
            lcd_channel_type=AUTO_UPGRADE_LCD_CHANNEL_TYPE,
            lcd_channel_num=AUTO_UPGRADE_LCD_CHANNEL_NUM,
        )
    except Exception:
        logger.warning(
            "Failed to broadcast auto-upgrade failure Net Message", exc_info=True
        )


def _resolve_auto_upgrade_change_tag(
    initial_version: str | None,
    current_version: str | None,
    initial_revision: str,
    current_revision: str,
) -> str:
    if initial_version != current_version:
        return current_version or "-"
    if initial_revision != current_revision:
        return _short_revision(current_revision)
    return "CLEAN"


def _send_auto_upgrade_check_message(status: str, change_tag: str) -> None:
    from apps.nodes.models import NetMessage

    timestamp = timezone.localtime(timezone.now()).strftime("%H:%M")
    subject = f"UP-CHECK {timestamp}"

    try:
        NetMessage.broadcast(
            subject=subject,
            body=f"{status[:16]} {change_tag}",
            lcd_channel_type=AUTO_UPGRADE_LCD_CHANNEL_TYPE,
            lcd_channel_num=AUTO_UPGRADE_LCD_CHANNEL_NUM,
        )
    except Exception:
        logger.warning(
            "Failed to broadcast auto-upgrade check Net Message", exc_info=True
        )


def _classify_auto_upgrade_failure(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        if _is_network_failure(exc):
            return "NETWORK"
        command = exc.cmd
        if isinstance(command, (list, tuple)):
            command_text = " ".join(str(item) for item in command)
        else:
            command_text = str(command)
        if "git" in command_text:
            return "GIT-ERROR"
        if "upgrade.sh" in command_text:
            return "UPGRADE-SCRIPT"
        return "SUBPROCESS"
    return exc.__class__.__name__


def _fetch_repository_state(
    base_dir: Path,
    branch: str,
    mode: AutoUpgradeMode,
    ops: AutoUpgradeOperations,
    state: AutoUpgradeState,
) -> AutoUpgradeRepositoryState | None:
    try:
        ops.git_fetch(base_dir, branch)
    except subprocess.CalledProcessError as exc:
        fetch_error_output = (exc.stderr or exc.stdout or "").strip()
        error_message = f"Git fetch failed (exit code {exc.returncode})"
        if fetch_error_output:
            error_message = f"{error_message}: {fetch_error_output}"
        append_auto_upgrade_log(base_dir, error_message)
        handled_network = _handle_network_failure_if_applicable(base_dir, exc)
        if handled_network:
            state.reset_network_failures = False
            _record_auto_upgrade_failure(base_dir, _classify_auto_upgrade_failure(exc))
            state.failure_recorded = True
        else:
            state.failure_recorded = True
        raise

    try:
        remote_revision = ops.resolve_remote_revision(base_dir, branch)
    except subprocess.CalledProcessError as exc:
        if _handle_network_failure_if_applicable(base_dir, exc):
            state.reset_network_failures = False
        raise

    skipped_revisions = _load_skipped_revisions(base_dir)
    if remote_revision in skipped_revisions:
        append_auto_upgrade_log(
            base_dir,
            f"Skipping auto-upgrade for blocked revision {remote_revision}",
        )
        ops.ensure_runtime_services(
            base_dir,
            restart_if_active=False,
            revert_on_failure=False,
            log_appender=append_auto_upgrade_log,
        )
        return None

    ci_status = _ci_status_for_revision(base_dir, remote_revision, branch=branch)
    if ci_status and ci_status != "success":
        append_auto_upgrade_log(
            base_dir,
            (
                "Skipping auto-upgrade; CI status is "
                f"{ci_status} for revision {remote_revision}"
            ),
        )
        _record_auto_upgrade_failure(base_dir, "CI-FAILING")
        state.failure_recorded = True
        ops.ensure_runtime_services(
            base_dir,
            restart_if_active=False,
            revert_on_failure=False,
            log_appender=append_auto_upgrade_log,
        )
        return None

    release_version, release_revision, release_pypi_url = _latest_release()
    remote_version = release_version or _read_remote_version(base_dir, branch)
    local_version = _read_local_version(base_dir)
    severity = _resolve_release_severity(remote_version)
    local_revision = _current_revision(base_dir)

    return AutoUpgradeRepositoryState(
        remote_revision=remote_revision,
        release_version=release_version,
        release_revision=release_revision,
        release_pypi_url=release_pypi_url,
        remote_version=remote_version,
        local_version=local_version,
        local_revision=local_revision,
        severity=severity,
    )


def _plan_auto_upgrade(
    base_dir: Path,
    mode: AutoUpgradeMode,
    repo_state: AutoUpgradeRepositoryState,
    notify: Callable[[str, str], Any] | None,
    startup: Callable[[], Any] | None,
    ops: AutoUpgradeOperations,
) -> tuple[list[str], bool] | None:
    upgrade_was_applied = False
    args: list[str] = []
    upgrade_subject = _resolve_upgrade_subject()
    upgrade_stamp = timezone.localtime(timezone.now()).strftime("@ %Y%m%d %H:%M")

    if not _canary_gate(base_dir, repo_state, mode):
        ops.ensure_runtime_services(
            base_dir,
            restart_if_active=False,
            revert_on_failure=False,
            log_appender=append_auto_upgrade_log,
        )
        if startup:
            startup()
        return None

    if mode.requires_pypi:
        if not repo_state.release_pypi_url:
            append_auto_upgrade_log(
                base_dir,
                "Skipping auto-upgrade; PyPI release has not been published yet.",
            )
            ops.ensure_runtime_services(
                base_dir,
                restart_if_active=False,
                revert_on_failure=False,
                log_appender=append_auto_upgrade_log,
            )
            if startup:
                startup()
            return None

    if mode.mode == "unstable":
        if repo_state.severity == SEVERITY_LOW:
            append_auto_upgrade_log(
                base_dir,
                "Skipping auto-upgrade for low severity patch on latest channel",
            )
            ops.ensure_runtime_services(
                base_dir,
                restart_if_active=False,
                revert_on_failure=False,
                log_appender=append_auto_upgrade_log,
            )
            if startup:
                startup()
            return None

        if (
            repo_state.local_revision == repo_state.remote_revision
            and repo_state.local_revision
        ):
            ops.ensure_runtime_services(
                base_dir,
                restart_if_active=False,
                revert_on_failure=False,
                log_appender=append_auto_upgrade_log,
            )
            return None

        if notify:
            notify(upgrade_subject, upgrade_stamp)
        args = _upgrade_command_args("latest")
        upgrade_was_applied = True
    else:
        target_version = repo_state.remote_version or repo_state.local_version or "0"

        if repo_state.local_version == target_version:
            ops.ensure_runtime_services(
                base_dir,
                restart_if_active=False,
                revert_on_failure=False,
                log_appender=append_auto_upgrade_log,
            )
            if startup:
                startup()
            return None

        if repo_state.release_version and repo_state.release_revision:
            matches_revision = False
            model = _get_package_release_model()
            if model is None:
                matches_revision = True
            else:
                matches_revision = model.matches_revision(
                    repo_state.release_version, repo_state.remote_revision
                )
            if not matches_revision:
                append_auto_upgrade_log(
                    base_dir,
                    (
                        "Skipping stable auto-upgrade; release revision does not "
                        "match origin/main"
                    ),
                )
                ops.ensure_runtime_services(
                    base_dir,
                    restart_if_active=False,
                    revert_on_failure=False,
                    log_appender=append_auto_upgrade_log,
                )
                return None

        if notify:
            notify(upgrade_subject, upgrade_stamp)
        args = _upgrade_command_args("stable")
        upgrade_was_applied = True

    if os.name != "nt" and args and args[0].lower().endswith(".bat"):
        args = ["./upgrade.sh", *args[1:]]
        append_auto_upgrade_log(
            base_dir,
            "Normalized upgrade command for POSIX host",
        )

    return args, upgrade_was_applied


def _execute_upgrade_plan(
    base_dir: Path,
    mode: AutoUpgradeMode,
    repo_state: AutoUpgradeRepositoryState,
    args: list[str],
    upgrade_was_applied: bool,
    log_file: Path,
    ops: AutoUpgradeOperations,
    state: AutoUpgradeState,
):
    if upgrade_was_applied and not mode.admin_override and not mode.skip_recency_check:
        if _auto_upgrade_ran_recently(base_dir, mode.interval_minutes):
            append_auto_upgrade_log(
                base_dir,
                (
                    "Skipping auto-upgrade; last run was less than "
                    f"{mode.interval_minutes} minutes ago"
                ),
            )
            ops.ensure_runtime_services(
                base_dir,
                restart_if_active=False,
                revert_on_failure=False,
                log_appender=append_auto_upgrade_log,
            )
            return

    with log_file.open("a") as fh:
        fh.write(f"{timezone.now().isoformat()} running: {' '.join(args)}\n")

    if (
        upgrade_was_applied
        and not mode.admin_override
        and not mode.skip_recency_check
        and _auto_upgrade_ran_recently(base_dir, mode.interval_minutes)
    ):
        append_auto_upgrade_log(
            base_dir,
            (
                "Skipping auto-upgrade; last run was less than "
                f"{mode.interval_minutes} minutes ago"
            ),
        )
        ops.ensure_runtime_services(
            base_dir,
            restart_if_active=False,
            revert_on_failure=False,
            log_appender=append_auto_upgrade_log,
        )
        return

    if upgrade_was_applied:
        _broadcast_upgrade_start_message(
            repo_state.local_revision, repo_state.remote_revision
        )
        _record_auto_upgrade_timestamp(base_dir)

    delegated_unit: str | None = None
    if ops.delegate_upgrade.__module__ != __name__:
        delegated_unit = ops.delegate_upgrade(base_dir, args)

    if delegated_unit:
        append_auto_upgrade_log(
            base_dir,
            (
                "Auto-upgrade delegated to systemd; review "
                f"journalctl -u {delegated_unit} for progress"
            ),
        )
        if upgrade_was_applied:
            append_auto_upgrade_log(
                base_dir,
                (
                    "Scheduled post-upgrade health check in %s seconds"
                    % AUTO_UPGRADE_HEALTH_DELAY_SECONDS
                ),
            )
            _schedule_health_check(1)
        return

    delegated_unit, ran_inline = ops.run_upgrade_command(base_dir, args)

    if delegated_unit:
        append_auto_upgrade_log(
            base_dir,
            (
                "Auto-upgrade delegated to systemd; review "
                f"journalctl -u {delegated_unit} for progress"
            ),
        )
        if upgrade_was_applied:
            append_auto_upgrade_log(
                base_dir,
                (
                    "Scheduled post-upgrade health check in %s seconds"
                    % AUTO_UPGRADE_HEALTH_DELAY_SECONDS
                ),
            )
            _schedule_health_check(1)
        return

    if not ran_inline:
        append_auto_upgrade_log(
            base_dir,
            "Delegated auto-upgrade launch failed; will retry on next cycle",
        )
        _record_auto_upgrade_failure(base_dir, "UPGRADE-LAUNCH")
        state.failure_recorded = True
        return

    ops.ensure_runtime_services(
        base_dir,
        restart_if_active=True,
        revert_on_failure=True,
        log_appender=append_auto_upgrade_log,
    )


def _handle_auto_upgrade_failure(
    base_dir: Path, exc: Exception, state: AutoUpgradeState
) -> None:
    if not state.failure_recorded:
        state.failure_recorded = True
        _record_auto_upgrade_failure(base_dir, _classify_auto_upgrade_failure(exc))


def _finalize_auto_upgrade(base_dir: Path, state: AutoUpgradeState) -> None:
    if state.reset_network_failures and not state.failure_recorded:
        _reset_network_failure_count(base_dir)
    if not state.failure_recorded:
        _reset_auto_upgrade_failure_count(base_dir)


@shared_task
def check_github_updates(
    channel_override: str | None = None,
    *,
    operations: AutoUpgradeOperations | None = None,
    manual_trigger: bool = False,
    policy_id: int | None = None,
) -> str:
    """Check the GitHub repo for updates and upgrade if needed."""

    base_dir = _project_base_dir()
    if manual_trigger:
        _ensure_git_safe_directory(base_dir)
    branch = "main"
    ops = operations or _default_auto_upgrade_operations()
    state = AutoUpgradeState()
    status = "FAILED"
    initial_version = _read_local_version(base_dir)
    initial_revision = _current_revision(base_dir)

    try:
        policy = _load_upgrade_policy(policy_id)
        if policy_id is not None and policy is None:
            append_auto_upgrade_log(
                base_dir,
                f"Skipping auto-upgrade; policy {policy_id} was not found.",
            )
            return "SKIPPED"

        mode = _resolve_auto_upgrade_mode(
            base_dir, channel_override, policy=policy
        )
        status = "NO-UPDATES"

        if not _apply_stable_schedule_guard(
            base_dir, mode, ops, append_auto_upgrade_log
        ):
            status = "SKIPPED"
            return status

        startup = None
        try:
            from apps.nodes.apps import _startup_notification as startup  # type: ignore
        except Exception:
            startup = None

        log_file = _log_auto_upgrade_trigger(base_dir)

        if mode.override_log:
            append_auto_upgrade_log(
                base_dir,
                f"Using admin override channel: {mode.override_log}",
            )
        if mode.policy_name:
            append_auto_upgrade_log(
                base_dir,
                f"Applying upgrade policy: {mode.policy_name}",
            )

        notify = None
        try:  # pragma: no cover - optional dependency
            from apps.core.notifications import notify  # type: ignore
        except Exception:
            notify = None

        if manual_trigger:
            _prepare_manual_auto_upgrade_repo(base_dir)

        repo_state = _fetch_repository_state(base_dir, branch, mode, ops, state)
        if repo_state is None:
            status = "SKIPPED"
            return status

        plan = _plan_auto_upgrade(base_dir, mode, repo_state, notify, startup, ops)
        if plan is None:
            status = "NO-UPDATES"
            return status

        args, upgrade_was_applied = plan

        status = "APPLIED" if upgrade_was_applied else "NO-UPDATES"

        _execute_upgrade_plan(
            base_dir,
            mode,
            repo_state,
            args,
            upgrade_was_applied,
            log_file,
            ops,
            state,
        )
    except Exception as exc:
        status = "FAILED"
        _handle_auto_upgrade_failure(base_dir, exc, state)
        raise
    finally:
        current_version = _read_local_version(base_dir)
        current_revision = _current_revision(base_dir)
        change_tag = _resolve_auto_upgrade_change_tag(
            initial_version, current_version, initial_revision, current_revision
        )
        _finalize_auto_upgrade(base_dir, state)
        _send_auto_upgrade_check_message(status, change_tag)
    return status


def _record_health_check_result(
    base_dir: Path, attempt: int, status: int | None, detail: str
) -> None:
    status_display = status if status is not None else "unreachable"
    message = "Health check attempt %s %s (%s)" % (attempt, detail, status_display)
    append_auto_upgrade_log(base_dir, message)


def _schedule_health_check(next_attempt: int) -> None:
    verify_auto_upgrade_health.apply_async(
        kwargs={"attempt": next_attempt},
        countdown=AUTO_UPGRADE_HEALTH_DELAY_SECONDS,
    )


def _current_revision(base_dir: Path) -> str:
    """Return the current git revision when available."""

    del base_dir  # Base directory handled by shared revision helper.

    try:
        from . import get_revision

        return get_revision()
    except Exception:  # pragma: no cover - defensive fallback
        logger.warning(
            "Failed to resolve git revision for auto-upgrade logging", exc_info=True
        )
        return ""


def _handle_failed_health_check(base_dir: Path, detail: str) -> None:
    revision = _current_revision(base_dir)
    if not revision:
        logger.warning(
            "Failed to determine revision during auto-upgrade health check failure"
        )

    _add_skipped_revision(base_dir, revision)
    append_auto_upgrade_log(
        base_dir, "Health check failed; manual intervention required"
    )
    _record_auto_upgrade_failure(base_dir, detail or "Health check failed")


@shared_task
def verify_auto_upgrade_health(attempt: int = 1) -> bool | None:
    """Verify the upgraded suite responds successfully.

    After the post-upgrade delay the site is probed once; any response other
    than HTTP 200 triggers an automatic revert and records the failing
    revision so future upgrade attempts skip it.
    """

    base_dir = _project_base_dir()
    url = _resolve_service_url(base_dir)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Arthexis-AutoUpgrade/1.0"},
    )

    status: int | None = None
    detail = "succeeded"
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = getattr(response, "status", response.getcode())
    except urllib.error.HTTPError as exc:
        status = exc.code
        detail = f"returned HTTP {exc.code}"
        logger.warning(
            "Auto-upgrade health check attempt %s returned HTTP %s", attempt, exc.code
        )
    except urllib.error.URLError as exc:
        detail = f"failed with {exc}"
        logger.warning(
            "Auto-upgrade health check attempt %s failed: %s", attempt, exc
        )
    except Exception as exc:  # pragma: no cover - unexpected network error
        detail = f"failed with {exc}"
        logger.exception(
            "Unexpected error probing suite during auto-upgrade attempt %s", attempt
        )
        _record_health_check_result(base_dir, attempt, status, detail)
        _handle_failed_health_check(base_dir, detail)
        return False

    if status == 200:
        _record_health_check_result(base_dir, attempt, status, "succeeded")
        logger.info(
            "Auto-upgrade health check succeeded on attempt %s with HTTP %s",
            attempt,
            status,
        )
        return True

    if detail == "succeeded":
        if status is not None:
            detail = f"returned HTTP {status}"
        else:
            detail = "failed with unknown status"

    _record_health_check_result(base_dir, attempt, status, detail)
    _handle_failed_health_check(base_dir, detail)
    return False


__all__ = [
    "AUTO_UPGRADE_HEALTH_DELAY_SECONDS",
    "AUTO_UPGRADE_LCD_CHANNEL_NUM",
    "AUTO_UPGRADE_LCD_CHANNEL_TYPE",
    "AutoUpgradeMode",
    "AutoUpgradeRepositoryState",
    "AutoUpgradeOperations",
    "check_github_updates",
    "verify_auto_upgrade_health",
    "_broadcast_upgrade_start_message",
    "_canary_gate",
    "_ci_status_for_revision",
    "_current_revision",
    "_project_base_dir",
    "_read_auto_upgrade_failure_count",
    "_resolve_auto_upgrade_change_tag",
    "_send_auto_upgrade_check_message",
    "SEVERITY_CRITICAL",
    "SEVERITY_LOW",
    "SEVERITY_NORMAL",
    "append_auto_upgrade_log",
]
