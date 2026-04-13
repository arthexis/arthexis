from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

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


def create_gallery_image(*, uploaded_file, title: str, description: str = "", include_in_public_gallery: bool = False, owner_user=None, owner_group=None) -> GalleryImage:
    bucket = get_gallery_bucket()
    media_file = create_media_file(bucket=bucket, uploaded_file=uploaded_file)
    return GalleryImage.objects.create(
        media_file=media_file,
        title=title,
        description=description,
        include_in_public_gallery=include_in_public_gallery,
        owner_user=owner_user,
        owner_group=owner_group,
    )
