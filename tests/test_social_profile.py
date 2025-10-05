from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from core.models import SocialProfile


User = get_user_model()


@pytest.mark.django_db
def test_bluesky_requires_handle_and_domain():
    user = User.objects.create(username="bluesky-owner")
    profile = SocialProfile(user=user, network=SocialProfile.Network.BLUESKY)

    with pytest.raises(ValidationError) as excinfo:
        profile.full_clean()

    errors = excinfo.value.message_dict
    assert "handle" in errors
    assert "domain" in errors


@pytest.mark.django_db
def test_discord_requires_bot_configuration():
    user = User.objects.create(username="discord-owner")
    profile = SocialProfile(user=user, network=SocialProfile.Network.DISCORD)

    with pytest.raises(ValidationError) as excinfo:
        profile.full_clean()

    errors = excinfo.value.message_dict
    assert "application_id" in errors
    assert "guild_id" in errors
    assert "bot_token" in errors


@pytest.mark.django_db
def test_discord_allows_multiple_profiles_without_handle_conflict():
    user_one = User.objects.create(username="discord-one")
    user_two = User.objects.create(username="discord-two")

    first = SocialProfile(
        user=user_one,
        network=SocialProfile.Network.DISCORD,
        application_id="1234567890",
        guild_id="0987654321",
        bot_token="token-one",
    )
    first.full_clean()
    first.save()

    second = SocialProfile(
        user=user_two,
        network=SocialProfile.Network.DISCORD,
        application_id="2234567890",
        guild_id="1987654321",
        bot_token="token-two",
    )
    second.full_clean()
    second.save()

    assert (
        SocialProfile.objects.filter(network=SocialProfile.Network.DISCORD).count() == 2
    )


@pytest.mark.django_db
def test_discord_fields_are_trimmed():
    user = User.objects.create(username="discord-trim")
    profile = SocialProfile(
        user=user,
        network=SocialProfile.Network.DISCORD,
        application_id="  3000000000  ",
        guild_id="  4000000000  ",
        public_key="  pk-test  ",
        bot_token="token-trim",
        default_channel_id="  5000000000  ",
    )
    profile.full_clean()
    profile.save()

    profile.refresh_from_db()
    assert profile.application_id == "3000000000"
    assert profile.guild_id == "4000000000"
    assert profile.public_key == "pk-test"
    assert profile.default_channel_id == "5000000000"


@pytest.mark.django_db
def test_discord_string_representation_uses_guild():
    user = User.objects.create(username="discord-str")
    profile = SocialProfile(
        user=user,
        network=SocialProfile.Network.DISCORD,
        application_id="6000000000",
        guild_id="7000000000",
        bot_token="token-str",
    )
    profile.full_clean()
    profile.save()

    assert str(profile) == "7000000000@discord"
