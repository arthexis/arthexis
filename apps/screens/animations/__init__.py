from __future__ import annotations

from collections.abc import Generator, Iterable, Iterator
from importlib import import_module, resources
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from apps.screens.models import LCDAnimation

ANIMATION_FRAME_COLUMNS = 16
ANIMATION_FRAME_ROWS = 2
ANIMATION_FRAME_CHARS = ANIMATION_FRAME_COLUMNS * ANIMATION_FRAME_ROWS
DEFAULT_ANIMATION_FILE = "scrolling_trees.txt"


class AnimationLoadError(ValueError):
    """Raised when an animation file or generator is invalid."""


def _resolve_path(candidate: str | Path) -> Path:
    path = Path(candidate)
    if path.is_absolute():
        return path

    try:
        package_files = resources.files(__name__)
    except (TypeError, FileNotFoundError):
        package_files = None

    if package_files:
        packaged = Path(package_files / str(candidate))
        if packaged.exists():
            return packaged

    return path


def _validate_frame(frame: str, *, index: int) -> str:
    if len(frame) != ANIMATION_FRAME_CHARS:
        raise AnimationLoadError(
            f"Frame {index} must be {ANIMATION_FRAME_CHARS} characters (got {len(frame)})."
        )
    return frame


def load_frames_from_file(candidate: str | Path) -> list[str]:
    """Load animation frames from a text file.

    Each line must contain exactly 32 characters (16 per LCD row). Spaces are
    preserved verbatim to allow sparse animations.
    """

    path = _resolve_path(candidate)
    data = path.read_text(encoding="utf-8").splitlines()
    frames = [_validate_frame(line, index=index) for index, line in enumerate(data, start=1)]
    if not frames:
        raise AnimationLoadError("Animation file must contain at least one frame.")
    return frames


def resolve_generator(target: str) -> Callable[[], Iterable[str] | Generator[str, None, None]]:
    """Resolve a ``module:function`` reference to an animation generator."""

    if ":" not in target:
        raise AnimationLoadError("Animation generator path must use 'module:function'.")

    module_path, func_name = target.rsplit(":", 1)
    module = import_module(module_path)
    try:
        func = getattr(module, func_name)
    except AttributeError as exc:  # pragma: no cover - defensive
        raise AnimationLoadError(f"Animation generator '{target}' not found.") from exc

    if not callable(func):
        raise AnimationLoadError(f"Animation generator '{target}' is not callable.")

    return func


def load_frames_from_callable(
    generator: Callable[[], Iterable[str] | Generator[str, None, None]]
) -> Iterator[str]:
    """Yield validated animation frames from a generator."""

    for index, frame in enumerate(generator(), start=1):
        yield _validate_frame(frame, index=index)


def load_frames_from_animation(animation: "LCDAnimation") -> Iterator[str]:
    """Yield frames for an :class:`~apps.screens.models.LCDAnimation` instance."""

    if animation.generator_path:
        generator = resolve_generator(animation.generator_path)
        yield from load_frames_from_callable(generator)
        return

    if animation.source_path:
        yield from load_frames_from_file(animation.source_path)
        return

    raise AnimationLoadError("Animation requires either a source file or generator path.")


def default_tree_frames() -> list[str]:
    """Load the bundled "Scrolling Trees" animation frames."""

    return load_frames_from_file(DEFAULT_ANIMATION_FILE)


__all__ = [
    "ANIMATION_FRAME_CHARS",
    "ANIMATION_FRAME_COLUMNS",
    "ANIMATION_FRAME_ROWS",
    "AnimationLoadError",
    "DEFAULT_ANIMATION_FILE",
    "default_tree_frames",
    "load_frames_from_animation",
    "load_frames_from_callable",
    "load_frames_from_file",
    "resolve_generator",
]
