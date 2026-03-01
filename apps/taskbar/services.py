"""Services for taskbar icon selection and lock file persistence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from .models import TaskbarIcon


class TaskbarIconError(Exception):
    """Base exception for taskbar icon operations."""


class TaskbarIconNotFoundError(TaskbarIconError):
    """Raised when a requested icon cannot be located."""


@dataclass(frozen=True)
class TaskbarIconSelection:
    """Resolved icon selection for taskbar rendering."""

    icon: TaskbarIcon
    source: str


class TaskbarIconSelector:
    """Encapsulate lock-file based taskbar icon selection behavior."""

    lock_file_name = "taskbar_icon.lck"

    def __init__(self, lock_dir: Path | None = None):
        """Initialize selector with optional custom lock directory."""

        base_lock_dir = Path(settings.BASE_DIR) / ".locks"
        self.lock_dir = lock_dir or base_lock_dir

    @property
    def lock_path(self) -> Path:
        """Return lock file path storing the current icon slug."""

        return self.lock_dir / self.lock_file_name

    def get_active_icon(self) -> TaskbarIconSelection:
        """Resolve active icon from lock file, falling back to default icon."""

        lock_slug = self._read_lock_slug()
        if lock_slug:
            icon = TaskbarIcon.objects.filter(slug=lock_slug).first()
            if icon:
                return TaskbarIconSelection(icon=icon, source="lock")

        default_icon = TaskbarIcon.objects.filter(is_default=True).first()
        if default_icon:
            return TaskbarIconSelection(icon=default_icon, source="default")

        icon = TaskbarIcon.objects.order_by("name").first()
        if icon:
            return TaskbarIconSelection(icon=icon, source="first")

        raise TaskbarIconNotFoundError("No taskbar icons are configured.")

    def set_active_icon(self, slug: str) -> TaskbarIcon:
        """Persist selected icon slug to lock file and return selected icon."""

        icon = TaskbarIcon.objects.filter(slug=slug).first()
        if icon is None:
            raise TaskbarIconNotFoundError(f"Taskbar icon '{slug}' was not found.")
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(slug, encoding="utf-8")
        return icon

    def clear_active_icon(self) -> None:
        """Remove lock file to restore default icon selection."""

        self.lock_path.unlink(missing_ok=True)

    def _read_lock_slug(self) -> str | None:
        """Read icon slug from lock file, ignoring invalid or missing values."""

        try:
            value = self.lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None


__all__ = [
    "TaskbarIconError",
    "TaskbarIconNotFoundError",
    "TaskbarIconSelection",
    "TaskbarIconSelector",
]
