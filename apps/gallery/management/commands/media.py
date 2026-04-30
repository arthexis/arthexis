from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.groups.models import SecurityGroup

from ...models import GalleryCategory, GalleryImage, GalleryImageTrait, GalleryTrait
from ...permissions import can_manage_gallery
from ...services import create_gallery_image


class Command(BaseCommand):
    help = "Manage gallery media and taxonomy."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action", required=True)

        upload = subparsers.add_parser("upload", help="Upload a gallery image")
        upload.add_argument("--username", required=True)
        upload.add_argument("--file", required=True)
        upload.add_argument("--title", required=True)
        upload.add_argument("--description", default="")
        upload.add_argument("--public", action="store_true", default=False)
        upload.add_argument(
            "--release-at",
            default="",
            help="ISO datetime when the image should become public (for example 2026-06-01T09:00:00+00:00).",
        )
        upload.add_argument("--as-content-sample", action="store_true", default=False)
        upload.add_argument("--owner-user", default="")
        upload.add_argument("--owner-group", default="")

        category = subparsers.add_parser("category", help="Create or update category")
        category.add_argument("--username", required=True)
        category.add_argument("--slug", required=True)
        category.add_argument("--name", required=True)
        category.add_argument("--description", default="")

        trait = subparsers.add_parser("trait", help="Create or update trait")
        trait.add_argument("--username", required=True)
        trait.add_argument("--slug", required=True)
        trait.add_argument("--name", required=True)
        trait.add_argument("--description", default="")

        assign = subparsers.add_parser("assign-trait", help="Assign trait to image")
        assign.add_argument("--username", required=True)
        assign.add_argument("--image", required=True)
        assign.add_argument("--trait", required=True)
        assign.add_argument("--category", default="")
        assign.add_argument("--label", default="")
        assign.add_argument("--value", type=float, default=1.0)

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(username=options["username"]).first()
        if user is None:
            raise CommandError("User not found.")
        if not can_manage_gallery(user):
            raise CommandError("User is not allowed to manage gallery media.")

        action = options["action"]
        if action == "upload":
            self._upload(options)
            return
        if action == "category":
            self._upsert_category(options)
            return
        if action == "trait":
            self._upsert_trait(options)
            return
        if action == "assign-trait":
            self._assign_trait(options)
            return

        raise CommandError("Unknown action")

    def _upload(self, options):
        owner_user = None
        owner_group = None
        owner_username = options.get("owner_user") or ""
        owner_group_name = options.get("owner_group") or ""
        if bool(owner_username) == bool(owner_group_name):
            raise CommandError("Pick exactly one owner user or owner group.")

        if owner_username:
            owner_user = get_user_model().objects.filter(username=owner_username).first()
            if owner_user is None:
                raise CommandError("Owner user not found.")

        if owner_group_name:
            owner_group = SecurityGroup.objects.filter(name=owner_group_name).first()
            if owner_group is None:
                raise CommandError("Owner group not found.")

        file_path = options["file"]
        with open(file_path, "rb") as handle:
            uploaded_file = File(handle, name=file_path.split("/")[-1])
            release_at_raw = (options.get("release_at") or "").strip()
            public_release_at = None
            if release_at_raw:
                parsed_release_at = parse_datetime(release_at_raw)
                if parsed_release_at is None:
                    raise CommandError("Invalid --release-at value; use ISO datetime format.")
                public_release_at = timezone.make_aware(parsed_release_at) if timezone.is_naive(parsed_release_at) else parsed_release_at
            elif options["public"]:
                public_release_at = timezone.now()
            image = create_gallery_image(
                uploaded_file=uploaded_file,
                title=options["title"],
                description=options["description"],
                public_release_at=public_release_at,
                create_content_sample=options["as_content_sample"],
                owner_user=owner_user,
                owner_group=owner_group,
            )
        self.stdout.write(self.style.SUCCESS(f"Uploaded image {image.slug}"))

    def _upsert_category(self, options):
        category, _ = GalleryCategory.objects.update_or_create(
            slug=options["slug"],
            defaults={"name": options["name"], "description": options["description"]},
        )
        self.stdout.write(self.style.SUCCESS(f"Saved category {category.slug}"))

    def _upsert_trait(self, options):
        trait, _ = GalleryTrait.objects.update_or_create(
            slug=options["slug"],
            defaults={"name": options["name"], "description": options["description"]},
        )
        self.stdout.write(self.style.SUCCESS(f"Saved trait {trait.slug}"))

    def _assign_trait(self, options):
        image = GalleryImage.objects.filter(slug=options["image"]).first()
        if image is None:
            raise CommandError("Image not found.")
        trait = GalleryTrait.objects.filter(slug=options["trait"]).first()
        if trait is None:
            raise CommandError("Trait not found.")
        category = None
        if options["category"]:
            category = GalleryCategory.objects.filter(slug=options["category"]).first()
            if category is None:
                raise CommandError("Category not found.")

        GalleryImageTrait.objects.update_or_create(
            image=image,
            category=category,
            trait=trait,
            qualitative_value=options["label"],
            defaults={"float_value": options["value"]},
        )
        self.stdout.write(self.style.SUCCESS("Trait assignment saved."))
