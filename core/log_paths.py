"""Helpers for selecting writable log directories."""

from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile


def _is_root() -> bool:
    if hasattr(os, "geteuid"):
        try:
            return os.geteuid() == 0
        except OSError:  # pragma: no cover - defensive for unusual platforms
            return False
    return False


def _state_home(base_home: Path) -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home).expanduser()
    return base_home / ".local" / "state"


def select_log_dir(base_dir: Path) -> Path:
    """Choose a writable log directory for the current process."""

    default = base_dir / "logs"
    env_override = os.environ.get("ARTHEXIS_LOG_DIR")
    is_root = _is_root()
    sudo_user = os.environ.get("SUDO_USER")

    candidates: list[Path] = []
    if env_override:
        candidates.append(Path(env_override).expanduser())

    if is_root:
        if not sudo_user or sudo_user == "root":
            candidates.append(default)
        candidates.append(Path("/var/log/arthexis"))
        candidates.append(Path("/tmp/arthexis/logs"))
    else:
        home: Path | None
        try:
            home = Path.home()
        except (RuntimeError, OSError, KeyError):
            home = None

        candidates.append(default)

        tmp_logs = Path(tempfile.gettempdir()) / "arthexis" / "logs"

        if home is not None:
            state_home = _state_home(home)
            candidates.extend(
                [
                    state_home / "arthexis" / "logs",
                    home / ".arthexis" / "logs",
                ]
            )
        else:
            candidates.append(tmp_logs)

        candidates.append(Path("/tmp/arthexis/logs"))
        candidates.append(tmp_logs)

    seen: set[Path] = set()
    ordered_candidates: list[Path] = []
    for candidate in candidates:
        candidate = candidate.expanduser()
        if candidate not in seen:
            seen.add(candidate)
            ordered_candidates.append(candidate)

    attempted: list[Path] = []
    chosen: Path | None = None
    for candidate in ordered_candidates:
        attempted.append(candidate)
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(candidate, os.W_OK | os.X_OK):
            chosen = candidate
            break

    if chosen is None:
        attempted_str = (
            ", ".join(str(path) for path in attempted) if attempted else "none"
        )
        raise RuntimeError(
            f"Unable to create a writable log directory. Tried: {attempted_str}"
        )

    if chosen != default:
        if (
            attempted
            and attempted[0] == default
            and not os.access(default, os.W_OK | os.X_OK)
        ):
            print(
                f"Log directory {default} is not writable; using {chosen}",
                file=sys.stderr,
            )
        elif is_root and sudo_user and sudo_user != "root" and not env_override:
            print(
                f"Running with elevated privileges; writing logs to {chosen}",
                file=sys.stderr,
            )

    os.environ["ARTHEXIS_LOG_DIR"] = str(chosen)
    return chosen
