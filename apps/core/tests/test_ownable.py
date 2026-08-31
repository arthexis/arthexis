"""Regression and ownership behavior tests for ownable models."""

import json

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms import ModelForm

from apps.core.admin.mixins import OwnableAdminForm, OwnableAdminMixin
from apps.core.models import (
    get_ownable_models,
    get_owned_objects_for_group,
    get_owned_objects_for_user,
)
from apps.groups.models import SecurityGroup
from apps.repos.models.github_tokens import GitHubToken
from apps.sigils.sigil_resolver import resolve_sigils


@pytest.mark.django_db
def test_ownable_clean_requires_owner():
    user = get_user_model().objects.create(username="owner-test")
    token = GitHubToken(label="Needs Owner", token="ghp_test")
    with pytest.raises(ValidationError):
        token.full_clean()
    token.user = user
    token.full_clean()  # does not raise

@pytest.mark.django_db
@pytest.mark.sigil_roots
def test_object_sigils_resolve_owner_and_members():
    user = get_user_model().objects.create(username="sigil-user")
    group = SecurityGroup.objects.create(name="Sigil Group")
    group.user_set.add(user)
    token = GitHubToken.objects.create(
        label="Sigil Token", token="ghp_sigil", group=group
    )

    resolved_owner = resolve_sigils("[OBJECT.OWNER]", current=token)
    assert resolved_owner == group.name

    resolved_members = resolve_sigils("[OBJECT.OWNERS]", current=token)
    members = json.loads(resolved_members)
    assert user.username in members

@pytest.mark.django_db
def test_owned_object_helpers_return_direct_and_indirect_lists():
    user = get_user_model().objects.create(username="owner-links")
    group = SecurityGroup.objects.create(name="Link Group")
    group.user_set.add(user)

    direct_token = GitHubToken.objects.create(
        label="Direct Token", token="ghp_direct", user=user
    )
    group_token = GitHubToken.objects.create(
        label="Group Token", token="ghp_group", group=group
    )

    direct, via = get_owned_objects_for_user(user)
    assert any(link.label == str(direct_token) for link in direct)
    assert any(link.label == str(group_token) and link.via == group.name for link in via)

    direct_group, via_members = get_owned_objects_for_group(group)
    assert any(link.label == str(group_token) for link in direct_group)
    assert any(link.via == user.username for link in via_members)

@pytest.mark.django_db
def test_ownable_admins_use_mixin():
    """Regression: every registered ownable admin should include OwnableAdminMixin."""

    registry = admin.site._registry
    for model in get_ownable_models():
        admin_instance = registry.get(model)
        if admin_instance is None:
            # Inline-only models are not registered directly with the admin site.
            continue
        assert isinstance(
            admin_instance, OwnableAdminMixin
        ), f"{model.__name__} admin must include OwnableAdminMixin"


@pytest.mark.django_db
def test_ownable_admin_form_supports_exactly_one_owner_configuration():
    class ExactlyOneGitHubTokenOwnerForm(OwnableAdminForm):
        owner_exactly_one = True
        owner_field_names = ("user", "group")

        class Meta:
            model = GitHubToken
            fields = "__all__"

    User = get_user_model()
    user = User.objects.create_user(username="exact-owner", email="exact-owner@example.com")
    group = SecurityGroup.objects.create(name="Exact Owner Group")

    no_owner_form = ExactlyOneGitHubTokenOwnerForm(
        data={
            "label": "Contractor token",
            "token": "ghp_no_owner",
        },
        instance=GitHubToken(),
    )
    assert not no_owner_form.is_valid()
    assert "Select exactly one owner." in no_owner_form.errors["user"][0]

    multiple_owner_form = ExactlyOneGitHubTokenOwnerForm(
        data={
            "user": user.pk,
            "group": group.pk,
            "label": "Contractor token",
            "token": "ghp_multiple_owner",
        },
        instance=GitHubToken(),
    )
    assert not multiple_owner_form.is_valid()
    assert "Select exactly one owner." in multiple_owner_form.errors["user"][0]


@pytest.mark.django_db
def test_ownable_admin_form_uses_non_field_error_when_owner_fields_absent():
    class NoOwnerFieldsForm(OwnableAdminForm):
        owner_field_names = ("missing_owner",)

        class Meta:
            model = GitHubToken
            fields = ("label", "token")

    form = NoOwnerFieldsForm(
        data={
            "label": "Contractor token",
            "token": "ghp_no_fields",
        },
        instance=GitHubToken(),
    )

    assert not form.is_valid()
    assert "__all__" in form.errors
    assert form.errors["__all__"][0]
    assert "user" not in form.errors
    assert "group" not in form.errors


@pytest.mark.django_db
def test_ownable_admin_form_handles_parent_clean_returning_none(monkeypatch):
    class DefensiveForm(OwnableAdminForm):
        class Meta:
            model = GitHubToken
            fields = "__all__"

    monkeypatch.setattr(ModelForm, "clean", lambda _self: None)

    form = DefensiveForm(
        data={
            "label": "Contractor token",
            "token": "ghp_defensive",
        },
        instance=GitHubToken(),
    )

    assert form.clean() is None
