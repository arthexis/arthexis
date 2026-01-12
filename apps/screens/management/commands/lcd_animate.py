from __future__ import annotations

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.screens.animations import AnimationLoadError, load_frames_from_animation
from apps.screens.lcd import LCDUnavailableError, prepare_lcd_controller
from apps.screens.lcd_screen import LCDFrameWriter
from apps.screens.models import LCDAnimation


class Command(BaseCommand):
    """Play a configured LCD animation or list available choices."""

    help = "Play the named LCD animation or list available animations"

    def add_arguments(self, parser):
        parser.add_argument("slug", nargs="?", help="Slug of the LCD animation to play")
        parser.add_argument(
            "--loops",
            type=int,
            default=1,
            help="Number of times to loop the animation (0 for infinite)",
        )
        parser.add_argument(
            "--interval",
            type=int,
            help="Override the frame interval in milliseconds",
        )

    def handle(self, *args, **options):
        slug = options.get("slug")

        if not slug:
            self._list_animations()
            return

        animation = self._load_animation(slug)
        frames = self._load_frames(animation)
        if not frames:
            raise CommandError(f"LCD animation '{slug}' did not yield any frames.")

        interval_ms = options.get("interval") or animation.frame_interval_ms
        loops = options.get("loops") or 0

        writer = self._setup_writer(Path(settings.BASE_DIR))
        self.stdout.write(
            f"Playing '{animation.name}' ({len(frames)} frames); press Ctrl+C to stop."
        )
        self._play_animation(frames, frame_interval_ms=interval_ms, loops=loops, writer=writer)

    # ------------------------------------------------------------------
    def _list_animations(self) -> None:
        animations = LCDAnimation.objects.all().order_by("name")
        if not animations.exists():
            self.stdout.write("No LCD animations configured.")
            return

        self.stdout.write("Available animations:")
        for animation in animations:
            status = "" if animation.is_active else " [inactive]"
            source = animation.generator_path or animation.source_path or "unspecified"
            self.stdout.write(f"- {animation.slug}: {animation.name}{status} ({source})")

    def _load_animation(self, slug: str) -> LCDAnimation:
        try:
            return LCDAnimation.objects.get(slug=slug)
        except LCDAnimation.DoesNotExist as exc:
            raise CommandError(f"LCD animation '{slug}' not found.") from exc

    def _load_frames(self, animation: LCDAnimation) -> list[str]:
        try:
            return list(load_frames_from_animation(animation))
        except AnimationLoadError as exc:
            raise CommandError(str(exc)) from exc

    def _setup_writer(self, base_dir: Path) -> LCDFrameWriter:
        try:
            lcd = prepare_lcd_controller(base_dir=base_dir)
        except LCDUnavailableError as exc:
            work_dir = Path(base_dir) / "work"
            work_dir.mkdir(parents=True, exist_ok=True)
            work_file = work_dir / "lcd-animate.txt"
            self.stdout.write(
                self.style.WARNING(
                    f"LCD unavailable ({exc}); writing frames to {work_file} instead."
                )
            )
            return LCDFrameWriter(None, work_file=work_file)

        return LCDFrameWriter(lcd)

    def _play_animation(
        self,
        frames: list[str],
        *,
        frame_interval_ms: int,
        loops: int,
        writer: LCDFrameWriter,
    ) -> None:
        interval_seconds = max(0, frame_interval_ms) / 1000
        iterations = 0
        try:
            while True:
                iterations += 1
                for frame in frames:
                    writer.write(frame[:16], frame[16:])
                    time.sleep(interval_seconds)

                if loops and iterations >= loops:
                    break
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Animation interrupted."))
