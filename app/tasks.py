"""Example Celery tasks."""

import logging
import subprocess
from pathlib import Path

import requests
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def heartbeat() -> None:
    """Log a simple heartbeat message."""
    logger.info("Heartbeat task executed")


@shared_task
def auto_upgrade(mode: str = "version") -> None:
    """Check for remote updates and run upgrade script when needed.

    Parameters
    ----------
    mode:
        Either ``"latest"`` to track the latest commit or ``"version"`` to
        compare the ``VERSION`` file.
    """

    base_dir = Path(__file__).resolve().parent.parent
    try:
        branch = (
            subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=base_dir,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        remote = (
            subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=base_dir,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - git failure
        logger.warning("Auto-upgrade git info failed: %s", exc)
        return

    # Build raw URLs for GitHub-style remotes
    remote = remote.rstrip(".git")
    raw_base = (
        remote.replace("git@github.com:", "https://raw.githubusercontent.com/")
        .replace("https://github.com/", "https://raw.githubusercontent.com/")
    )
    version_url = f"{raw_base}/{branch}/VERSION"

    try:
        resp = requests.get(version_url, timeout=10)
        resp.raise_for_status()
    except Exception as exc:  # pragma: no cover - network failure
        logger.warning("Auto-upgrade version check failed: %s", exc)
        return

    local_version = "0"
    version_file = base_dir / "VERSION"
    if version_file.exists():
        local_version = version_file.read_text().strip()
    remote_version = resp.text.strip()

    if mode == "latest":
        api_base = (
            remote.replace("git@github.com:", "https://api.github.com/repos/")
            .replace("https://github.com/", "https://api.github.com/repos/")
        )
        commits_url = f"{api_base}/commits/{branch}"
        try:
            commit_resp = requests.get(commits_url, timeout=10)
            commit_resp.raise_for_status()
            remote_sha = commit_resp.json().get("sha", "")
        except Exception as exc:  # pragma: no cover - network failure
            logger.warning("Auto-upgrade commit check failed: %s", exc)
            return
        local_sha = (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=base_dir,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        if remote_sha != local_sha:
            subprocess.run([str(base_dir / "upgrade.sh"), "--latest"], check=True)
    else:
        if remote_version != local_version:
            subprocess.run([str(base_dir / "upgrade.sh")], check=True)

