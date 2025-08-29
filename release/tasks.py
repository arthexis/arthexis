from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from celery import shared_task


@shared_task
def check_github_updates() -> None:
    """Check the GitHub repo for updates and upgrade if needed."""
    base_dir = Path(__file__).resolve().parent.parent
    mode_file = base_dir / "AUTO_UPGRADE"
    mode = "version"
    if mode_file.exists():
        mode = mode_file.read_text().strip()

    branch = "main"
    subprocess.run(["git", "fetch", "origin", branch], cwd=base_dir, check=True)

    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "auto-upgrade.log"
    with log_file.open("a") as fh:
        fh.write(f"{datetime.utcnow().isoformat()} check_github_updates triggered\n")

    lcd_lock = base_dir / "locks" / "lcd_screen.lck"
    has_lcd = lcd_lock.exists()
    notify = None
    startup = None
    if has_lcd:
        from core.notifications import notify  # type: ignore
        from nodes.apps import _startup_notification as startup  # type: ignore

    if mode == "latest":
        local = subprocess.check_output(["git", "rev-parse", branch], cwd=base_dir).decode().strip()
        remote = subprocess.check_output([
            "git",
            "rev-parse",
            f"origin/{branch}",
        ], cwd=base_dir).decode().strip()
        if local == remote:
            if has_lcd and startup:
                startup()
            return
        if has_lcd and notify:
            notify("Upgrading...", "")
        args = ["./upgrade.sh", "--latest", "--no-restart"]
    else:
        local = "0"
        version_file = base_dir / "VERSION"
        if version_file.exists():
            local = version_file.read_text().strip()
        remote = subprocess.check_output([
            "git",
            "show",
            f"origin/{branch}:VERSION",
        ], cwd=base_dir).decode().strip()
        if local == remote:
            if has_lcd and startup:
                startup()
            return
        if has_lcd and notify:
            notify("Upgrading...", "")
        args = ["./upgrade.sh", "--no-restart"]

    with log_file.open("a") as fh:
        fh.write(f"{datetime.utcnow().isoformat()} running: {' '.join(args)}\n")

    subprocess.run(args, cwd=base_dir, check=True)

    service_file = base_dir / "locks/service.lck"
    if service_file.exists():
        service = service_file.read_text().strip()
        subprocess.run([
            "sudo",
            "systemctl",
            "kill",
            "--signal=TERM",
            service,
        ])
    else:
        subprocess.run(["pkill", "-f", "manage.py runserver"])

