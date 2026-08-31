import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cards.models import RFID


@pytest.mark.django_db
def test_pre_register_profile_creates_missing_cards_without_mutating_existing(tmp_path):
    existing = RFID.objects.create(rfid="EXISTING1", allowed=False)
    profile = tmp_path / "initial-profile.toml"
    profile.write_text(
        "[rfid]\npre_register = [\"existing1\", \"new-card-2\", \"new-card-2\"]\n",
        encoding="utf-8",
    )

    call_command("rfid", "pre-register", "--profile", profile)

    existing.refresh_from_db()
    assert existing.allowed is False
    assert RFID.objects.filter(rfid="EXISTING1").count() == 1
    assert RFID.objects.filter(rfid="NEW-CARD-2").count() == 1


@pytest.mark.django_db
def test_pre_register_profile_rejects_invalid_toml_before_writing_cards(tmp_path):
    profile = tmp_path / "initial-profile.toml"
    profile.write_text("[rfid\npre_register = [\"BAD\"]\n", encoding="utf-8")

    with pytest.raises(CommandError, match="not valid TOML"):
        call_command("rfid", "pre-register", "--profile", profile)

    assert RFID.objects.count() == 0


@pytest.mark.django_db
def test_pre_register_profile_requires_rfid_table(tmp_path):
    profile = tmp_path / "initial-profile.toml"
    profile.write_text("[rfids]\npre_register = [\"BAD\"]\n", encoding="utf-8")

    with pytest.raises(CommandError, match=r"must contain an \[rfid\] table"):
        call_command("rfid", "pre-register", "--profile", profile)

    assert RFID.objects.count() == 0
