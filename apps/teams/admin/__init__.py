from django.contrib import admin

from apps.core.admin import InviteLeadAdmin

from ..models import InviteLead, SlackBotProfile
from .fixtures import (
    EntityModelAdmin,
    UserDatumAdminMixin,
    delete_user_fixture,
    dump_user_fixture,
    _fixture_path,
    _resolve_fixture_user,
    _user_allows_user_data,
)
from .slack import SlackBotProfileAdmin


@admin.register(InviteLead)
class InviteLeadAdminProxy(InviteLeadAdmin):
    pass


admin.site.register(SlackBotProfile, SlackBotProfileAdmin)

__all__ = [
    "EntityModelAdmin",
    "UserDatumAdminMixin",
    "delete_user_fixture",
    "dump_user_fixture",
    "_fixture_path",
    "_resolve_fixture_user",
    "_user_allows_user_data",
    "InviteLeadAdminProxy",
    "SlackBotProfileAdmin",
]
