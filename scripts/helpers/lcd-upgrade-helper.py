#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

MESSAGE_LINE = "RUN upgrade.sh"
SLEEP_SECONDS = 2.0


def resolve_base_dir() -> Path:
    env_base = os.getenv("ARTHEXIS_BASE_DIR")
    if env_base:
        return Path(env_base)

    return Path(__file__).resolve().parents[2]


def _sentinel_paths(base_dir: Path) -> list[Path]:
    git_dir = base_dir / ".git"
    refs: list[Path] = [git_dir / "FETCH_HEAD", git_dir / "HEAD", git_dir / "index"]

    head_ref = None
    try:
        head_ref = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        head_ref = None

    if head_ref and head_ref.startswith("ref: "):
        ref_path = git_dir / head_ref.split("ref: ", 1)[1]
        refs.append(ref_path)

    return refs


def _snapshot(paths: Iterable[Path]) -> dict[Path, float | None]:
    snapshot: dict[Path, float | None] = {}
    for path in paths:
        try:
            snapshot[path] = path.stat().st_mtime
        except OSError:
            snapshot[path] = None
    return snapshot


def _changes_detected(snapshot: dict[Path, float | None]) -> bool:
    for path, initial in snapshot.items():
        try:
            current = path.stat().st_mtime
        except OSError:
            current = None

        if initial != current:
            return True
    return False


def _init_lcd():
    try:
        from apps.screens.lcd import CharLCD1602, LCDUnavailableError
    except Exception:
        return None

    if CharLCD1602 is None:
        return None

    try:
        lcd = CharLCD1602()
        lcd.init_lcd()
        lcd.reset()
    except LCDUnavailableError:
        return None
    except Exception:
        return None

    return lcd


def _lcd_columns(default: int = 16) -> int:
    try:
        from apps.screens.lcd import CharLCD1602
    except Exception:
        return default

    return getattr(CharLCD1602, "columns", default)


def _lcd_service_active(base_dir: Path) -> bool:
    service_name = None
    service_lock = base_dir / ".locks" / "service.lck"
    if service_lock.exists():
        try:
            service_name = service_lock.read_text(encoding="utf-8").strip()
        except OSError:
            service_name = None

    systemctl = shutil.which("systemctl")
    if systemctl and service_name:
        result = subprocess.run(
            [systemctl, "is-active", f"lcd-{service_name}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return True

    pgrep = shutil.which("pgrep")
    if not pgrep:
        return False

    for pattern in ("python -m apps.screens.lcd_screen", "apps/screens/lcd_screen.py"):
        result = subprocess.run(
            [pgrep, "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return True

    return False


def _write_upgrade_lock(base_dir: Path, message: str) -> None:
    try:
        from apps.screens.startup_notifications import render_lcd_lock_file
    except Exception:
        return

    lock_dir = base_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "lcd-high"
    try:
        lock_file.write_text(render_lcd_lock_file(subject=message, body=""), encoding="utf-8")
    except Exception:
        return


def _display_loop(base_dir: Path, snapshot: dict[Path, float | None]) -> None:
    lcd = _init_lcd()
    columns = _lcd_columns()
    line = MESSAGE_LINE.ljust(columns)[:columns]

    fallback_path = base_dir / "work" / "lcd-upgrade-helper.txt"
    fallback_path.parent.mkdir(parents=True, exist_ok=True)

    while True:
        if _lcd_service_active(base_dir):
            lcd = None
            _write_upgrade_lock(base_dir, MESSAGE_LINE)
        elif lcd is None:
            lcd = _init_lcd()

        try:
            if lcd is not None:
                lcd.write(0, 0, line)
                lcd.write(0, 1, " " * len(line))
        except Exception:
            lcd = None

        try:
            fallback_path.write_text(f"{line}\n{' ' * len(line)}\n", encoding="utf-8")
        except Exception:
            pass

        if _changes_detected(snapshot):
            break

        time.sleep(SLEEP_SECONDS)


def main() -> int:
    base_dir = resolve_base_dir()
    sentinel_snapshot = _snapshot(_sentinel_paths(base_dir))
    _display_loop(base_dir, sentinel_snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
