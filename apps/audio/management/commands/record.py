from __future__ import annotations

from datetime import datetime
import signal
import shutil
import subprocess
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.audio.utils import AUDIO_DIR, has_audio_capture_device, save_audio_sample
from apps.audio.models import RecordingDevice
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


class Command(BaseCommand):
    """Record audio from the default microphone until stopped."""

    help = "Record audio from the default microphone until a key is pressed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sample-rate",
            type=int,
            default=16_000,
            help="Sample rate to use when recording (Hz).",
        )
        parser.add_argument(
            "--channels",
            type=int,
            default=1,
            help="Number of channels to record (1 for mono, 2 for stereo).",
        )

    def handle(self, *args, **options):
        node = Node.get_local()
        if node is None:
            raise CommandError("No local node is registered; cannot record audio.")

        try:
            feature = NodeFeature.objects.get(slug="audio-capture")
        except NodeFeature.DoesNotExist:
            raise CommandError("The audio-capture node feature is not configured.")

        if not feature.is_enabled:
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
            self.stdout.write(
                self.style.SUCCESS(
                    "Enabled the audio-capture feature for the local node."
                )
            )

        created, updated = RecordingDevice.refresh_from_system(node=node)
        if created or updated:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Detected {created} new and {updated} existing recording devices."
                )
            )

        if not has_audio_capture_device():
            raise CommandError("No audio recording devices were detected on this node.")

        sample_rate = options["sample_rate"]
        channels = options["channels"]
        preferred_device = RecordingDevice.preferred_device()
        path = self._record_until_keypress(
            sample_rate=sample_rate,
            channels=channels,
            device_identifier=preferred_device.identifier if preferred_device else None,
        )

        sample = save_audio_sample(
            path, node=node, method="COMMAND_RECORD", link_duplicates=True
        )
        saved_path = Path(sample.path if sample else path).as_posix()

        if not sample:
            self.stdout.write(self.style.WARNING("Duplicate audio sample; not saved."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Audio sample saved to {saved_path}"))

        self.stdout.write(saved_path)
        return saved_path

    def _record_until_keypress(
        self,
        *,
        sample_rate: int,
        channels: int,
        device_identifier: str | None = None,
    ) -> Path:
        tool_path = shutil.which("arecord")
        if not tool_path:
            raise CommandError("arecord is not available; cannot record audio.")

        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        filename = AUDIO_DIR / f"{datetime.utcnow():%Y%m%d%H%M%S}-{uuid.uuid4().hex}.wav"

        alsa_device = (
            RecordingDevice.identifier_to_alsa_device(device_identifier)
            if device_identifier
            else None
        )
        command = [
            tool_path,
            "-q",
        ]
        if alsa_device:
            command.extend(["-D", alsa_device])
        command.extend(
            [
                "-f",
                "S16_LE",
                "-r",
                str(sample_rate),
                "-c",
                str(channels),
                str(filename),
            ]
        )

        self.stdout.write(self.style.NOTICE("Press Enter to stop recording."))
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            input()
        except KeyboardInterrupt:
            process.send_signal(signal.SIGINT)
            process.wait()
            filename.unlink(missing_ok=True)
            raise CommandError("Recording cancelled.")

        process.send_signal(signal.SIGINT)
        try:
            stdout_data, stderr_data = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout_data, stderr_data = process.communicate()

        if process.returncode not in (0, -2):
            error = (stderr_data or stdout_data or "Audio capture failed").strip()
            filename.unlink(missing_ok=True)
            raise CommandError(error)

        if not filename.exists():
            raise CommandError("Audio capture failed to produce a file.")

        return filename
