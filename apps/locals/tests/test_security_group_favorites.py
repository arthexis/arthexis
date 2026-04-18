import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from apps.groups.models import SecurityGroup
from apps.locals.models import (
    Favorite,
    PRODUCT_DEVELOPER_FAVORITE_TARGETS,
    SITE_OPERATOR_FAVORITE_TARGETS,
    ensure_security_group_favorites,
)


def _target_content_type_ids(targets):
    content_type_ids = []
    for app_label, model_name in targets:
        model = django_apps.get_model(app_label, model_name)
        content_type_ids.append(ContentType.objects.get_for_model(model).pk)
    return set(content_type_ids)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("group_names", "expected_targets"),
    [
        (["Product Developer"], PRODUCT_DEVELOPER_FAVORITE_TARGETS),
        (["Site Operator"], SITE_OPERATOR_FAVORITE_TARGETS),
        (
            ["Product Developer", "Site Operator"],
            PRODUCT_DEVELOPER_FAVORITE_TARGETS + SITE_OPERATOR_FAVORITE_TARGETS,
        ),
    ],
)
def test_ensure_security_group_favorites_assigns_expected_models(group_names, expected_targets):
    user_model = get_user_model()
    user = user_model.objects.create_user(username="favorite-user", password="pw", is_staff=True)
    for group_name in group_names:
        group, _ = SecurityGroup.objects.get_or_create(name=group_name)
        user.groups.add(group)

    ensure_security_group_favorites(user)

    expected_content_type_ids = _target_content_type_ids(dict.fromkeys(expected_targets))
    assigned_content_type_ids = set(
        Favorite.objects.filter(user=user).values_list("content_type_id", flat=True)
    )
    assert expected_content_type_ids.issubset(assigned_content_type_ids)


@pytest.mark.django_db
def test_ensure_security_group_favorites_is_idempotent():
    user_model = get_user_model()
    user = user_model.objects.create_user(username="idempotent-user", password="pw", is_staff=True)
    product_developer, _ = SecurityGroup.objects.get_or_create(name="Product Developer")
    user.groups.add(product_developer)

    ensure_security_group_favorites(user)
    ensure_security_group_favorites(user)

    target_count = len(PRODUCT_DEVELOPER_FAVORITE_TARGETS)
    assert Favorite.objects.filter(user=user).count() == target_count


@pytest.mark.django_db
def test_user_group_assignment_seeds_favorites():
    user_model = get_user_model()
    user = user_model.objects.create_user(username="group-seeded-user", password="pw", is_staff=True)
    site_operator, _ = SecurityGroup.objects.get_or_create(name="Site Operator")

    user.groups.add(site_operator)

    target_count = len(SITE_OPERATOR_FAVORITE_TARGETS)
    assert Favorite.objects.filter(user=user).count() == target_count


@pytest.mark.django_db
def test_group_user_set_assignment_seeds_favorites():
    user_model = get_user_model()
    user = user_model.objects.create_user(username="group-reverse-user", password="pw", is_staff=True)
    site_operator, _ = SecurityGroup.objects.get_or_create(name="Site Operator")

    site_operator.user_set.add(user)

    target_count = len(SITE_OPERATOR_FAVORITE_TARGETS)
    assert Favorite.objects.filter(user=user).count() == target_count
