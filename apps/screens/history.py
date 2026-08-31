"""History helpers for capturing and replaying LCD frames."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Callable, Iterable, List


@dataclass
class HistoryEntry:
    timestamp: datetime
    line1: str
    line2: str
    label: str | None = None


class LCDHistoryRecorder:
    """Persist LCD frames to daily history files for replay."""

    def __init__(
        self,
        *,
        base_dir: Path,
        history_dir_name: str = "work",
        max_days: int = 3,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_dir = base_dir
        self.history_dir = base_dir / history_dir_name
        self.max_days = max_days
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = self._detect_current_history_date()
        self._rotate_if_needed()

    # ------------------------------------------------------------------
    def record(
        self,
        line1: str,
        line2: str,
        *,
        label: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        now = (timestamp or self.clock()).astimezone(timezone.utc)
        self._rotate_if_needed(now)

        entry = {
            "ts": now.isoformat(),
            "line1": line1,
            "line2": line2,
            "label": label,
        }

        history_file = self._history_path(0)
        try:
            with history_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            # History recording must never break the LCD updater; swallow
            # filesystem errors quietly.
            return

    # ------------------------------------------------------------------
    def _rotate_if_needed(self, now: datetime | None = None) -> None:
        now = (now or self.clock()).astimezone(timezone.utc)
        today = now.date()
        if self._current_date == today:
            return

        for index in range(self.max_days - 1, -1, -1):
            path = self._history_path(index)
            if index == self.max_days - 1:
                if path.exists():
                    path.unlink()
                continue

            next_path = self._history_path(index + 1)
            if path.exists():
                next_path.parent.mkdir(parents=True, exist_ok=True)
                path.rename(next_path)

        self._history_path(0).touch()
        self._current_date = today

    def _history_path(self, index: int) -> Path:
        return self.history_dir / f"lcd-history-{index}.txt"

    def _detect_current_history_date(self) -> date | None:
        history_path = self._history_path(0)
        try:
            stat_result = history_path.stat()
        except FileNotFoundError:
            return None
        except OSError:
            return None

        return datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc).date()


def load_history_entries(base_dir: Path, *, history_dir_name: str = "work") -> list[HistoryEntry]:
    history_dir = base_dir / history_dir_name
    entries: List[HistoryEntry] = []
    if not history_dir.exists():
        return entries

    for index in range(3):
        path = history_dir / f"lcd-history-{index}.txt"
        if not path.exists():
            continue

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        for line in lines:
            try:
                parsed = json.loads(line)
                ts = datetime.fromisoformat(parsed["ts"]).astimezone(timezone.utc)
                entries.append(
                    HistoryEntry(
                        timestamp=ts,
                        line1=str(parsed.get("line1", "")),
                        line2=str(parsed.get("line2", "")),
                        label=parsed.get("label"),
                    )
                )
            except Exception:
                continue

    entries.sort(key=lambda item: item.timestamp)
    return entries


def select_entry_for_timestamp(
    entries: Iterable[HistoryEntry], target: datetime
) -> HistoryEntry | None:
    ordered = sorted(entries, key=lambda item: item.timestamp)
    target = target.astimezone(timezone.utc)
    chosen: HistoryEntry | None = None
    for entry in ordered:
        if entry.timestamp <= target:
            chosen = entry
        else:
            break
    return chosen or (ordered[-1] if ordered else None)
