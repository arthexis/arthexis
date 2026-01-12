import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command

from apps.locals import user_data
from apps.locals.models import Favorite


@pytest.mark.django_db(transaction=True)
def test_user_data_persisted_and_reloaded_after_db_flush(tmp_path):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="alice", password="password", data_path=str(tmp_path)
    )
    content_type = ContentType.objects.get_for_model(user_model)

    favorite = Favorite.objects.create(
        user=user,
        content_type=content_type,
        custom_label="Example",
        priority=1,
    )
    Favorite.all_objects.filter(pk=favorite.pk).update(is_user_data=True)
    favorite.refresh_from_db()

    user_data.dump_user_fixture(favorite, user)
    fixture_path = user_data._fixture_path(user, favorite)

    assert fixture_path.exists()

    call_command("flush", verbosity=0, interactive=False)

    user = user_model.objects.create_user(
        username="alice", password="password", data_path=str(tmp_path)
    )
    ContentType.objects.get_for_model(user_model)
    ContentType.objects.get_for_model(Favorite)

    user_data.load_user_fixtures(user)

    restored = Favorite.objects.get(user=user)
    assert restored.custom_label == "Example"
    assert restored.is_user_data is True


@pytest.mark.django_db(transaction=True)
def test_user_data_applied_after_seed_fixture(tmp_path):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="alice", password="password", data_path=str(tmp_path)
    )
    content_type = ContentType.objects.get_for_model(user_model)

    seed_path = tmp_path / "seed_favorite.json"
    seed_payload = [
        {
            "model": "locals.favorite",
            "pk": 9999,
            "fields": {
                "user": user.pk,
                "content_type": content_type.pk,
                "custom_label": "Seed label",
                "user_data": False,
                "priority": 1,
                "is_seed_data": True,
            },
        }
    ]
    seed_path.write_text(json.dumps(seed_payload))

    user_fixture_path = user_data._data_dir(user) / "locals_favorite_9999.json"
    user_fixture_payload = [
        {
            "model": "locals.favorite",
            "pk": 9999,
            "fields": {
                "user": user.pk,
                "content_type": content_type.pk,
                "custom_label": "User label",
                "user_data": True,
                "priority": 2,
                "is_user_data": True,
            },
        }
    ]
    user_fixture_path.write_text(json.dumps(user_fixture_payload))

    call_command("load_user_data", str(seed_path), verbosity=0)

    seeded = Favorite.objects.get(pk=9999)
    assert seeded.custom_label == "Seed label"
    assert seeded.is_seed_data is True
    assert seeded.is_user_data is False

    user_data.load_user_fixtures(user)

    updated = Favorite.objects.get(pk=9999)
    assert updated.custom_label == "User label"
    assert updated.is_user_data is True
    assert updated.is_seed_data is True


@pytest.mark.django_db(transaction=True)
def test_user_data_fixture_skips_unknown_models(tmp_path):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="alice", password="password", data_path=str(tmp_path)
    )
    content_type = ContentType.objects.get_for_model(Favorite)

    fixture_path = user_data._data_dir(user) / "locals_favorite_123.json"
    fixture_payload = [
        {"model": "missing.model", "pk": 9999, "fields": {}},
        {
            "model": "locals.favorite",
            "pk": 123,
            "fields": {
                "user": user.pk,
                "content_type": content_type.pk,
                "custom_label": "Example",
                "user_data": True,
                "priority": 1,
                "is_user_data": True,
            },
        },
    ]
    fixture_path.write_text(json.dumps(fixture_payload))

    user_data.load_user_fixtures(user)

    restored = Favorite.objects.get(pk=123)
    assert restored.custom_label == "Example"
    assert restored.is_user_data is True
