import pytest
from django.contrib.admin.sites import AdminSite

from apps.chats.admin import ChatAvatarAdmin
from apps.chats.models import ChatAvatar


def test_chat_avatar_admin_has_no_social_profile_inlines() -> None:
    """Verify ChatAvatarAdmin returns no social profile inlines.

    Parameters:
        None.

    Returns:
        None: Confirms the admin built with ``AdminSite`` keeps inline classes empty.
    """

    site = AdminSite()
    admin = ChatAvatarAdmin(ChatAvatar, site)

    assert admin.get_inlines(None, None) == []
