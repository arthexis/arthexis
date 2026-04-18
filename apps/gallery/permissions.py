from django.contrib.auth.models import AnonymousUser

from .constants import GALLERY_MANAGER_GROUP_NAME


def can_manage_gallery(user) -> bool:
    if user is None or isinstance(user, AnonymousUser) or not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.groups.filter(name=GALLERY_MANAGER_GROUP_NAME).exists()
