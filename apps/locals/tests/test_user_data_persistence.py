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
