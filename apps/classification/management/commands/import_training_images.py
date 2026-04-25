"""Import local image folders as classifier training samples."""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify
from PIL import Image, UnidentifiedImageError

from apps.classification.ingest import (
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_IMAGE_PATTERNS,
    create_media_file_from_path,
)
from apps.classification.models import ClassificationTag, TrainingSample
from apps.media.models import MediaFile
from apps.media.utils import ensure_media_bucket

IMAGE_EXTENSIONS = set(SUPPORTED_IMAGE_EXTENSIONS)


@dataclass
class ImportStats:
    """Counters reported by the import command."""

    scanned: int = 0
    imported: int = 0
    reused: int = 0
    samples_created: int = 0
    samples_existing: int = 0
    tags_created: int = 0
    skipped_extension: int = 0
    skipped_unreadable: int = 0


class Command(BaseCommand):
    help = "Import a local image folder into classification training samples."

    def add_arguments(self, parser) -> None:
        parser.add_argument("path", help="Folder containing images to import.")
        parser.add_argument(
            "--bucket-slug",
            default="classification-training",
            help="Media bucket slug for imported training images.",
        )
        parser.add_argument(
            "--bucket-name",
            default="Classification Training Images",
            help="Media bucket display name for imported training images.",
        )
        parser.add_argument(
            "--tag",
            help="Use one explicit tag slug for every imported image.",
        )
        parser.add_argument(
            "--label-source",
            choices=("parent-directory", "top-directory"),
            default="parent-directory",
            help="Derive labels from each image's parent folder or top-level folder.",
        )
        parser.add_argument(
            "--split",
            choices=[choice for choice, _label in TrainingSample.Split.choices],
            default=TrainingSample.Split.TRAIN,
            help="Dataset split assigned to created samples.",
        )
        parser.add_argument(
            "--verified",
            action="store_true",
            help="Mark created training samples as verified.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Scan and report without creating media files, tags, or samples.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Stop after importing this many readable images.",
        )

    def handle(self, *args, **options) -> None:
        root = Path(options["path"]).expanduser().resolve()
        if not root.exists():
            raise CommandError(f"Import path does not exist: {root}")
        if not root.is_dir():
            raise CommandError(f"Import path is not a directory: {root}")
        if options["limit"] is not None and options["limit"] < 1:
            raise CommandError("--limit must be greater than zero.")

        bucket = None
        if not options["dry_run"]:
            bucket = ensure_media_bucket(
                slug=options["bucket_slug"],
                name=options["bucket_name"],
                allowed_patterns=SUPPORTED_IMAGE_PATTERNS,
            )

        stats = ImportStats()
        explicit_tag = self._explicit_tag(options.get("tag"))
        self._tag_cache: dict[str, ClassificationTag] = {}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            stats.scanned += 1
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                stats.skipped_extension += 1
                continue
            if not self._is_readable_image(path):
                stats.skipped_unreadable += 1
                continue
            if options["dry_run"]:
                stats.imported += 1
                if options["limit"] and stats.imported >= options["limit"]:
                    break
                continue

            assert bucket is not None
            media_file, created = self._get_or_create_media_file(
                root=root,
                path=path,
                bucket_slug=options["bucket_slug"],
                bucket_name=options["bucket_name"],
            )
            if created:
                stats.imported += 1
            else:
                stats.reused += 1

            tag, tag_created = self._get_or_create_tag(
                root=root,
                path=path,
                explicit_tag=explicit_tag,
                label_source=options["label_source"],
            )
            if tag_created:
                stats.tags_created += 1

            sample, sample_created = TrainingSample.objects.get_or_create(
                media_file=media_file,
                tag=tag,
                defaults={
                    "split": options["split"],
                    "source": self._compact_value(
                        f"folder-import:{path.relative_to(root).as_posix()}",
                        TrainingSample._meta.get_field("source").max_length,
                    ),
                    "is_verified": bool(options["verified"]),
                },
            )
            if sample_created:
                stats.samples_created += 1
            else:
                stats.samples_existing += 1
                updates = {}
                if sample.split != options["split"]:
                    updates["split"] = options["split"]
                if options["verified"] and not sample.is_verified:
                    updates["is_verified"] = True
                if updates:
                    TrainingSample.objects.filter(pk=sample.pk).update(**updates)

            if options["limit"] and (stats.imported + stats.reused) >= options["limit"]:
                break

        self.stdout.write(self._summary(stats=stats, root=root, dry_run=options["dry_run"]))

    def _explicit_tag(self, value: str | None) -> tuple[str, str] | None:
        if not value:
            return None
        slug = slugify(value)
        if not slug:
            raise CommandError("--tag must contain at least one slug character.")
        return slug, value.replace("-", " ").replace("_", " ").strip().title() or slug

    def _get_or_create_media_file(
        self,
        *,
        root: Path,
        path: Path,
        bucket_slug: str,
        bucket_name: str,
    ) -> tuple[MediaFile, bool]:
        original_name = self._compact_value(
            path.relative_to(root).as_posix(),
            MediaFile._meta.get_field("original_name").max_length,
        )
        size = path.stat().st_size
        existing = self._find_matching_media_file(
            bucket__slug=bucket_slug,
            original_name=original_name,
            size=size,
            path=path,
        )
        if existing:
            return existing, False
        media_file = create_media_file_from_path(
            path,
            bucket_slug=bucket_slug,
            bucket_name=bucket_name,
            original_name=original_name,
            queue_for_classification=False,
        )
        return media_file, True

    def _find_matching_media_file(self, *, path: Path, **filters) -> MediaFile | None:
        for media_file in MediaFile.objects.filter(**filters):
            if self._media_file_matches_path(media_file=media_file, path=path):
                return media_file
        return None

    def _media_file_matches_path(self, *, media_file: MediaFile, path: Path) -> bool:
        if not media_file.file:
            return False
        try:
            media_file.file.open("rb")
            with media_file.file as stored_file, path.open("rb") as incoming_file:
                while stored_chunk := stored_file.read(1024 * 1024):
                    if stored_chunk != incoming_file.read(len(stored_chunk)):
                        return False
                return incoming_file.read(1) == b""
        except (OSError, ValueError):
            return False

    def _get_or_create_tag(
        self,
        *,
        root: Path,
        path: Path,
        explicit_tag: tuple[str, str] | None,
        label_source: str,
    ) -> tuple[ClassificationTag, bool]:
        if explicit_tag is not None:
            slug, name = explicit_tag
        else:
            relative = path.relative_to(root)
            if label_source == "top-directory" and len(relative.parts) > 1:
                label = relative.parts[0]
            else:
                label = path.parent.name if path.parent != root else root.name
            name = label.replace("-", " ").replace("_", " ").strip().title() or "Unlabeled"
            slug = slugify(label) or "unlabeled"
            slug = self._compact_value(slug, ClassificationTag._meta.get_field("slug").max_length)
            name = self._compact_value(name, ClassificationTag._meta.get_field("name").max_length)

        cached_tag = self._tag_cache.get(slug)
        if cached_tag is not None:
            return cached_tag, False

        tag, tag_created = ClassificationTag.objects.get_or_create(slug=slug, defaults={"name": name})
        self._tag_cache[slug] = tag
        return tag, tag_created

    def _is_readable_image(self, path: Path) -> bool:
        try:
            with Image.open(path) as image:
                image.verify()
        except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
            return False
        return True

    def _compact_value(self, value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value
        digest = f"{zlib.crc32(value.encode('utf-8')):08x}"
        suffix_width = max(max_length - len(digest) - 1, 1)
        return f"{digest}:{value[-suffix_width:]}"

    def _summary(self, *, stats: ImportStats, root: Path, dry_run: bool) -> str:
        mode = "dry-run" if dry_run else "import"
        return (
            f"{mode} complete for {root}: "
            f"scanned={stats.scanned}, imported={stats.imported}, reused={stats.reused}, "
            f"samples_created={stats.samples_created}, samples_existing={stats.samples_existing}, "
            f"tags_created={stats.tags_created}, skipped_extension={stats.skipped_extension}, "
            f"skipped_unreadable={stats.skipped_unreadable}"
        )
