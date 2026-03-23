from __future__ import annotations

from collections.abc import Iterable, Iterator
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from apps.screens.models import LCDAnimation

ANIMATION_FRAME_COLUMNS = 16
ANIMATION_FRAME_ROWS = 2
ANIMATION_FRAME_CHARS = ANIMATION_FRAME_COLUMNS * ANIMATION_FRAME_ROWS
DEFAULT_ANIMATION_FILE = "scrolling_trees.txt"
ALLOWED_ANIMATION_SUFFIX = ".txt"


class AnimationLoadError(ValueError):
    """Raised when an animation file is invalid or unsupported."""


def _animations_root() -> Path:
    """Return the local filesystem path for the packaged animations directory."""

    return Path(resources.files(__name__))


def get_approved_animation_source(candidate: str | Path) -> Path:
    """Resolve *candidate* to a packaged animation file.

    Parameters:
        candidate: File name stored on an ``LCDAnimation`` row.

    Returns:
        The packaged animation path.

    Raises:
        AnimationLoadError: If the candidate is blank, traverses directories,
            uses a non-text format, or does not match a packaged animation file.
    """

    raw_candidate = str(candidate).strip()
    if not raw_candidate:
        raise AnimationLoadError("Animation source path is required.")

    relative_path = Path(raw_candidate)
    if relative_path.is_absolute() or len(relative_path.parts) != 1:
        raise AnimationLoadError("Animation source path must name a packaged file in apps/screens/animations/.")

    if relative_path.suffix != ALLOWED_ANIMATION_SUFFIX:
        raise AnimationLoadError("Animation source path must reference a packaged .txt file.")

    packaged_path = _animations_root() / relative_path.name
    if not packaged_path.exists() or not packaged_path.is_file():
        raise AnimationLoadError(f"Animation source '{raw_candidate}' is not a packaged animation file.")

    return packaged_path


def validate_animation_frame(frame: str, *, index: int) -> str:
    """Validate one LCD animation frame.

    Parameters:
        frame: Candidate frame text.
        index: 1-based frame index for error reporting.

    Returns:
        The validated frame.

    Raises:
        AnimationLoadError: If the frame does not contain exactly 32 characters.
    """

    if len(frame) != ANIMATION_FRAME_CHARS:
        raise AnimationLoadError(
            f"Frame {index} must be {ANIMATION_FRAME_CHARS} characters (got {len(frame)})."
        )
    return frame


def validate_animation_frames(frames: Iterable[str]) -> list[str]:
    """Validate a collection of animation frames.

    Parameters:
        frames: Iterable of LCD frame strings.

    Returns:
        The validated frames as a list.

    Raises:
        AnimationLoadError: If the iterable is empty or any frame is malformed.
    """

    validated = [validate_animation_frame(frame, index=index) for index, frame in enumerate(frames, start=1)]
    if not validated:
        raise AnimationLoadError("Animation file must contain at least one frame.")
    return validated


def load_frames_from_file(candidate: str | Path) -> list[str]:
    """Load validated animation frames from an approved packaged file.

    Parameters:
        candidate: File name stored on the animation record.

    Returns:
        A list of validated 32-character frames.

    Raises:
        AnimationLoadError: If the source is unsupported or contains malformed frames.
    """

    path = get_approved_animation_source(candidate)
    return validate_animation_frames(path.read_text(encoding="utf-8").splitlines())


def load_frames_from_animation(animation: "LCDAnimation") -> Iterator[str]:
    """Yield frames for an ``LCDAnimation`` instance.

    Parameters:
        animation: Animation model instance using a packaged source file.

    Returns:
        An iterator over validated frames.

    Raises:
        AnimationLoadError: If the animation does not point to an approved packaged file.
    """

    if animation.source_path:
        yield from load_frames_from_file(animation.source_path)
        return

    raise AnimationLoadError("Animation requires a packaged source file.")


def default_tree_frames() -> list[str]:
    """Load the bundled "Scrolling Trees" animation frames."""

    return load_frames_from_file(DEFAULT_ANIMATION_FILE)


__all__ = [
    "ANIMATION_FRAME_CHARS",
    "ANIMATION_FRAME_COLUMNS",
    "ANIMATION_FRAME_ROWS",
    "ALLOWED_ANIMATION_SUFFIX",
    "AnimationLoadError",
    "DEFAULT_ANIMATION_FILE",
    "default_tree_frames",
    "get_approved_animation_source",
    "load_frames_from_animation",
    "load_frames_from_file",
    "validate_animation_frame",
    "validate_animation_frames",
]
