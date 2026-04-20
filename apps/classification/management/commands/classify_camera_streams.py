"""Run the experimental image classifier against active camera streams."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.classification.models import ImageClassifierModel
from apps.classification.pipeline import classify_stream
from apps.video.models import MjpegStream


class Command(BaseCommand):
    help = "Capture and classify one frame for one or more active camera streams."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--stream",
            help="Optional stream primary key or slug. Defaults to all active streams.",
        )
        parser.add_argument(
            "--classifier",
            help="Optional classifier primary key or slug. Defaults to the selected model.",
        )

    def handle(self, *args, **options) -> None:
        classifier = self._resolve_classifier(options.get("classifier"))
        if classifier is None:
            raise CommandError("No selected ready image classifier is available.")
        streams = self._resolve_streams(options.get("stream"))
        if not streams:
            raise CommandError("No active MJPEG streams matched the request.")

        classified_streams = 0
        failed_streams = 0
        prediction_records = 0
        for stream in streams:
            try:
                _media_file, records = classify_stream(stream, classifier=classifier)
            except Exception as exc:
                failed_streams += 1
                self.stderr.write(
                    self.style.WARNING(f"{stream.slug}: classification failed ({exc}).")
                )
                continue
            if not records:
                continue
            classified_streams += 1
            prediction_records += len(records)
            self.stdout.write(
                self.style.SUCCESS(
                    f"{stream.slug}: created {len(records)} classification record(s)."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Completed camera classification for {classified_streams} stream(s); "
                f"created {prediction_records} record(s); "
                f"failed {failed_streams} stream(s)."
            )
        )

    def _resolve_classifier(self, value: str | None) -> ImageClassifierModel | None:
        if not value:
            return ImageClassifierModel.selected_general_model()
        queryset = ImageClassifierModel.objects.all()
        classifier = queryset.filter(pk=int(value)).first() if value.isdigit() else None
        if classifier is None:
            classifier = queryset.filter(slug=value).first()
        if classifier is None:
            raise CommandError(f"No classifier matched '{value}'.")
        return classifier

    def _resolve_streams(self, value: str | None) -> list[MjpegStream]:
        queryset = MjpegStream.objects.filter(is_active=True).order_by("name")
        if not value:
            return list(queryset)
        stream = queryset.filter(pk=int(value)).first() if value.isdigit() else None
        if stream is None:
            stream = queryset.filter(slug=value).first()
        return [stream] if stream else []
