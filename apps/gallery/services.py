from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.content.models import ContentSample
from apps.content.utils import save_content_sample
from apps.groups.models import SecurityGroup
from apps.media.utils import create_media_file, ensure_media_bucket

from .constants import GALLERY_BUCKET_SLUG
from .models import GalleryImage


def get_gallery_bucket():
    return ensure_media_bucket(slug=GALLERY_BUCKET_SLUG, name="Gallery Images")


def resolve_owner(owner_username: str | None, owner_group_name: str | None):
    owner_user = None
    owner_group = None
    if owner_username:
        owner_user = get_user_model().objects.filter(username=owner_username).first()
        if owner_user is None:
            raise ValidationError({"owner_user": "Owner username was not found."})
    if owner_group_name:
        owner_group = SecurityGroup.objects.filter(name=owner_group_name).first()
        if owner_group is None:
            raise ValidationError({"owner_group": "Owner group was not found."})
    if owner_user and owner_group:
        raise ValidationError({"owner": "Choose either owner user or owner group."})
    return owner_user, owner_group


def _save_gallery_content_sample(*, media_path: str, owner_user=None) -> ContentSample | None:
    return save_content_sample(
        path=Path(media_path),
        kind=ContentSample.IMAGE,
        method="GAL_UPLOAD",
        user=owner_user,
        link_duplicates=True,
        duplicate_log_context="gallery image upload",
    )


def create_gallery_image(
    *,
    uploaded_file,
    title: str,
    description: str = "",
    include_in_public_gallery: bool = False,
    create_content_sample: bool = False,
    owner_user=None,
    owner_group=None,
) -> GalleryImage:
    if bool(owner_user) == bool(owner_group):
        raise ValidationError({"owner": "Choose exactly one owner user or owner group."})

    bucket = get_gallery_bucket()
    media_file = create_media_file(bucket=bucket, uploaded_file=uploaded_file)
    try:
        with transaction.atomic():
            content_sample = None
            if create_content_sample:
                content_sample = _save_gallery_content_sample(
                    media_path=media_file.file.path,
                    owner_user=owner_user,
                )
            return GalleryImage.objects.create(
                media_file=media_file,
                content_sample=content_sample,
                title=title,
                description=description,
                include_in_public_gallery=include_in_public_gallery,
                owner_user=owner_user,
                owner_group=owner_group,
            )
    except Exception:
        if media_file.file:
            media_file.file.delete(save=False)
        media_file.delete()
        raise
