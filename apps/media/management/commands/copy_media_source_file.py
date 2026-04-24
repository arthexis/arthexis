"""Copy a local source file into suite-managed media storage."""

from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.media.models import MediaSourceFile
from apps.media.utils import copy_media_source_file_from_path


class Command(BaseCommand):
    help = "Copy a local source file, such as a .mse-set package, into managed media storage."

    def add_arguments(self, parser) -> None:
        parser.add_argument("path", help="Local source file path to copy into the suite.")
        parser.add_argument("--name", help="Optional display name for the source file.")
        parser.add_argument(
            "--source-type",
            choices=[choice for choice, _label in MediaSourceFile.SourceType.choices],
            default=MediaSourceFile.SourceType.MSE_SET,
            help="Type of source file being copied.",
        )
        parser.add_argument(
            "--allow-any-extension",
            action="store_true",
            help="Allow non-.mse-set files when source type is mse_set.",
        )

    def handle(self, *args, **options) -> None:
        source_path = Path(options["path"]).expanduser()
        source_type = options["source_type"]
        if (
            source_type == MediaSourceFile.SourceType.MSE_SET
            and source_path.suffix.lower() != ".mse-set"
            and not options["allow_any_extension"]
        ):
            raise CommandError("MSE source files must use the .mse-set extension.")

        try:
            media_source = copy_media_source_file_from_path(
                source_path,
                name=options.get("name"),
                source_type=source_type,
            )
        except (FileNotFoundError, IsADirectoryError) as exc:
            raise CommandError(str(exc)) from exc
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages) or str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Copied source file {media_source.pk}: {media_source.original_name} "
                f"({media_source.size} bytes)."
            )
        )
