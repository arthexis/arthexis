import json

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.chats.models import ChatAvatar
from apps.core.admin.mixins import OwnableAdminMixin
from apps.core.models import (
    get_owned_objects_for_group,
    get_owned_objects_for_user,
    get_ownable_models,
)
from apps.groups.models import SecurityGroup
from apps.sigils.sigil_resolver import resolve_sigils


@pytest.mark.django_db
def test_ownable_clean_requires_owner():
    user = get_user_model().objects.create(username="owner-test")
    avatar = ChatAvatar(name="Needs Owner")
    with pytest.raises(ValidationError):
        avatar.full_clean()
    avatar.user = user
    avatar.full_clean()  # does not raise


@pytest.mark.django_db
def test_object_sigils_resolve_owner_and_members():
    user = get_user_model().objects.create(username="sigil-user")
    group = SecurityGroup.objects.create(name="Sigil Group")
    group.user_set.add(user)
    avatar = ChatAvatar.objects.create(name="Sigil Avatar", group=group)

    resolved_owner = resolve_sigils("[OBJECT.OWNER]", current=avatar)
    assert resolved_owner == group.name

    resolved_members = resolve_sigils("[OBJECT.OWNERS]", current=avatar)
    members = json.loads(resolved_members)
    assert user.username in members


@pytest.mark.django_db
def test_owned_object_helpers_return_direct_and_indirect_lists():
    user = get_user_model().objects.create(username="owner-links")
    group = SecurityGroup.objects.create(name="Link Group")
    group.user_set.add(user)

    direct_avatar = ChatAvatar.objects.create(name="Direct Avatar", user=user)
    group_avatar = ChatAvatar.objects.create(name="Group Avatar", group=group)

    direct, via = get_owned_objects_for_user(user)
    assert any(link.label == str(direct_avatar) for link in direct)
    assert any(link.label == str(group_avatar) and link.via == group.name for link in via)

    direct_group, via_members = get_owned_objects_for_group(group)
    assert any(link.label == str(group_avatar) for link in direct_group)
    assert any(link.via == user.username for link in via_members)


@pytest.mark.django_db
def test_ownable_admins_use_mixin():
    registry = admin.site._registry
    for model in get_ownable_models():
        admin_instance = registry.get(model)
        if admin_instance is None:
            # Inline-only models are not registered directly with the admin site.
            continue
        assert isinstance(
            admin_instance, OwnableAdminMixin
        ), f"{model.__name__} admin must include OwnableAdminMixin"
