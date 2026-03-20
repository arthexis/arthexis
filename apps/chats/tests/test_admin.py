import pytest
from django.contrib.admin.sites import AdminSite

from apps.chats.admin import ChatAvatarAdmin
from apps.chats.models import ChatAvatar


pytestmark = pytest.mark.pr_origin(6311)


def test_chat_avatar_admin_has_no_social_profile_inlines() -> None:
    site = AdminSite()
    admin = ChatAvatarAdmin(ChatAvatar, site)

    assert admin.get_inlines(None, None) == []
