from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cards.models import RFID
from apps.energy.models import CustomerAccount
from apps.imager.initial_profile import (
    InitialProfileError,
    RedirectSnapshot,
    _apply_redirect,
    load_initial_profile,
)
from apps.ocpp.models import Charger


def write_profile(tmp_path, content: str):
    profile = tmp_path / "initial-profile.toml"
    profile.write_text(content, encoding="utf-8")
    return profile


def test_load_initial_profile_rejects_unknown_sections(tmp_path):
    profile = write_profile(
        tmp_path,
        "[rfid]\npre_register = []\n[unknown]\nvalue = true\n",
    )

    with pytest.raises(InitialProfileError, match="unsupported section"):
        load_initial_profile(profile)


def test_load_initial_profile_rejects_redirect_without_targets(tmp_path):
    profile = write_profile(
        tmp_path,
        '[rfid]\npre_register = []\n[ocpp_redirect]\ncharger_ip = "192.0.2.10"\n',
    )

    with pytest.raises(InitialProfileError, match="ocpp_redirect.targets"):
        load_initial_profile(profile)


@pytest.mark.parametrize(
    "setting, value",
    [("listen_port", "8888.9"), ("table", '"nat"')],
)
def test_load_initial_profile_rejects_unsafe_redirect_settings(
    tmp_path, setting, value
):
    profile = write_profile(
        tmp_path,
        f"""[rfid]
pre_register = []

[ocpp_redirect]
charger_ip = "192.0.2.10"
targets = ["198.51.100.11"]
{setting} = {value}
""",
    )

    with pytest.raises(InitialProfileError):
        load_initial_profile(profile)


def test_load_initial_profile_rejects_rfid_that_conflicts_with_autostart(tmp_path):
    profile = write_profile(
        tmp_path,
        """[rfid]
pre_register = ["B2A1"]

[charger]
id = "EVR-CONFLICT"
path = "/ocpp-j/EVR-CONFLICT"

[auto_start]
id_tag = "A1B2"
""",
    )

    with pytest.raises(InitialProfileError, match="conflicts with an RFID"):
        load_initial_profile(profile)


def test_load_initial_profile_rejects_rfids_outside_a_node_label_range(tmp_path):
    rfids = ", ".join(f'"card-{index}"' for index in range(101))
    profile = write_profile(
        tmp_path,
        f"""[node]
number = 4

[rfid]
pre_register = [{rfids}]
""",
    )

    with pytest.raises(InitialProfileError, match="more than 100 RFID"):
        load_initial_profile(profile)


def test_load_initial_profile_rejects_node_labels_outside_autofield_range(tmp_path):
    profile = write_profile(
        tmp_path,
        """[node]
number = 2147484

[rfid]
pre_register = ["card"]
""",
    )

    with pytest.raises(InitialProfileError, match="labels exceed"):
        load_initial_profile(profile)


@pytest.mark.django_db
def test_initial_profile_reconciles_rfid_autostart_and_scoped_redirect(tmp_path):
    profile = write_profile(
        tmp_path,
        """[node]
number = 4

[network]
copy_host_profiles = ["Site A", "Site B"]

[rfid]
pre_register = ["existing-card", "new-card"]
fallback_account = true

[charger]
id = "EVR-004"
path = "/ocpp-j/EVR-004"
connectors = [1]

[auto_start]
id_tag = "TALLER"

[ocpp_redirect]
interface = "eth0"
charger_ip = "192.0.2.10"
targets = ["198.51.100.11", "198.51.100.12"]
target_port = 80
listen_port = 8888
""",
    )
    existing = RFID.objects.create(rfid="EXISTING-CARD", allowed=False)
    completed = SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch("apps.imager.initial_profile._run_nft", return_value=completed),
        patch("apps.imager.initial_profile._write_redirect_service"),
        patch("apps.imager.initial_profile.subprocess.run", return_value=completed),
    ):
        output = StringIO()
        call_command(
            "imager", "initial-profile", "--apply", "--profile", profile, stdout=output
        )

    existing.refresh_from_db()
    assert existing.allowed is False
    new_card = RFID.objects.get(rfid="NEW-CARD")
    assert new_card.label_id == 4010
    assert (
        RFID.objects.get(rfid="NEW-CARD")
        .energy_accounts.filter(name="RFID FALLBACK ACCOUNT")
        .exists()
    )
    account = CustomerAccount.objects.get(ocpp_id_tag="TALLER")
    assert account.service_account is True
    assert (
        Charger.objects.filter(charger_id="EVR-004", auto_start_id_tag="TALLER").count()
        == 2
    )
    assert "rfids_created=1" in output.getvalue()
    assert "fallback_cards_bound=2" in output.getvalue()
    assert "redirect_applied=1" in output.getvalue()


@pytest.mark.django_db
def test_initial_profile_uses_node_number_for_new_rfid_label_range(tmp_path):
    profile = write_profile(
        tmp_path,
        """[node]
number = 4

[rfid]
pre_register = ["fallback-one", "fallback-two"]
""",
    )

    call_command("imager", "initial-profile", "--apply", "--profile", profile)

    assert list(
        RFID.objects.filter(rfid__in=["FALLBACK-ONE", "FALLBACK-TWO"])
        .order_by("label_id")
        .values_list("label_id", flat=True)
    ) == [4000, 4010]


@pytest.mark.django_db
def test_initial_profile_refuses_conflicting_node_rfid_label_range(tmp_path):
    RFID.objects.create(label_id=4000, rfid="OCCUPIED-CARD")
    profile = write_profile(
        tmp_path,
        """[node]
number = 4

[rfid]
pre_register = ["fallback-one"]
""",
    )

    with pytest.raises(CommandError, match="label 4000 is already assigned"):
        call_command("imager", "initial-profile", "--apply", "--profile", profile)

    assert not RFID.objects.filter(rfid="FALLBACK-ONE").exists()


@pytest.mark.django_db
def test_initial_profile_refuses_to_overwrite_an_existing_autostart_tag(tmp_path):
    Charger.objects.create(charger_id="EVR-CONFLICT", auto_start_id_tag="OTHER")
    profile = write_profile(
        tmp_path,
        """[rfid]
pre_register = []

[charger]
id = "EVR-CONFLICT"
path = "/ocpp-j/EVR-CONFLICT"

[auto_start]
id_tag = "TALLER"
""",
    )

    with pytest.raises(
        CommandError, match="conflicts with existing charger configuration"
    ):
        call_command("imager", "initial-profile", "--apply", "--profile", profile)


@pytest.mark.django_db
def test_initial_profile_leaves_existing_autostart_tag_when_none_is_requested(tmp_path):
    existing = Charger.objects.create(
        charger_id="EVR-EXISTING", auto_start_id_tag="OTHER"
    )
    profile = write_profile(
        tmp_path,
        """[rfid]
pre_register = []

[charger]
id = "EVR-EXISTING"
path = "/ocpp-j/EVR-EXISTING"
""",
    )

    call_command("imager", "initial-profile", "--apply", "--profile", profile)

    existing.refresh_from_db()
    assert existing.auto_start_id_tag == "OTHER"


@pytest.mark.django_db
def test_initial_profile_fallback_does_not_reassign_an_accounted_card(tmp_path):
    profile = write_profile(
        tmp_path,
        """[rfid]
pre_register = ["customer-card", "fallback-card"]
fallback_account = true
""",
    )
    customer_card = RFID.objects.create(rfid="CUSTOMER-CARD")
    customer = CustomerAccount.objects.create(name="Customer account")
    customer.rfids.add(customer_card)

    call_command("imager", "initial-profile", "--apply", "--profile", profile)

    customer_card.refresh_from_db()
    assert customer_card.energy_accounts.get() == customer
    fallback_card = RFID.objects.get(rfid="FALLBACK-CARD")
    assert fallback_card.energy_accounts.get().name == "RFID FALLBACK ACCOUNT"


@pytest.mark.django_db
def test_initial_profile_rolls_back_database_changes_after_a_conflict(tmp_path):
    Charger.objects.create(charger_id="EVR-ROLLBACK", auto_start_id_tag="OTHER")
    profile = write_profile(
        tmp_path,
        """[node]
number = 4

[rfid]
pre_register = ["ROLLBACK-CARD"]

[charger]
id = "EVR-ROLLBACK"
path = "/ocpp-j/EVR-ROLLBACK"

[auto_start]
id_tag = "TALLER"
""",
    )

    with patch.object(RFID, "_reset_label_sequence") as reset_sequence:
        with pytest.raises(
            CommandError, match="conflicts with existing charger configuration"
        ):
            call_command("imager", "initial-profile", "--apply", "--profile", profile)

    assert not RFID.objects.filter(rfid="ROLLBACK-CARD").exists()
    reset_sequence.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_initial_profile_resets_node_label_sequence_after_commit(tmp_path):
    profile = write_profile(
        tmp_path,
        """[node]
number = 4

[rfid]
pre_register = ["NODE-CARD"]
""",
    )

    with patch(
        "apps.imager.initial_profile._reset_node_rfid_label_sequence"
    ) as reset_sequence:
        call_command("imager", "initial-profile", "--apply", "--profile", profile)

    reset_sequence.assert_called_once_with()


@pytest.mark.django_db(transaction=True)
def test_initial_profile_retries_a_failed_node_label_sequence_reset(tmp_path):
    profile = write_profile(
        tmp_path,
        """[node]
number = 4

[rfid]
pre_register = ["NODE-CARD"]

[ocpp_redirect]
charger_ip = "192.0.2.10"
targets = ["198.51.100.11"]
""",
    )
    snapshot = RedirectSnapshot("", None, None, None, None, False)

    with (
        patch("apps.imager.initial_profile._validate_redirect"),
        patch("apps.imager.initial_profile._apply_redirect", return_value=snapshot),
        patch("apps.imager.initial_profile._restore_redirect") as restore,
        patch.object(
            RFID, "_reset_label_sequence", side_effect=[RuntimeError("failed"), None]
        ) as reset_sequence,
    ):
        call_command("imager", "initial-profile", "--apply", "--profile", profile)
        call_command("imager", "initial-profile", "--apply", "--profile", profile)

    assert RFID.objects.filter(rfid="NODE-CARD").exists()
    assert reset_sequence.call_count == 2
    restore.assert_not_called()


@pytest.mark.django_db
def test_initial_profile_defaults_to_validation_without_mutating_host(tmp_path):
    profile = write_profile(
        tmp_path,
        """[rfid]
pre_register = ["CHECK-ONLY-CARD"]

[charger]
id = "EVR-CHECK-ONLY"
path = "/ocpp-j/EVR-CHECK-ONLY"

[auto_start]
id_tag = "TALLER"

[ocpp_redirect]
charger_ip = "192.0.2.10"
targets = ["198.51.100.11"]
""",
    )

    output = StringIO()
    call_command("imager", "initial-profile", "--profile", profile, stdout=output)

    assert not RFID.objects.filter(rfid="CHECK-ONLY-CARD").exists()
    assert not Charger.objects.filter(charger_id="EVR-CHECK-ONLY").exists()
    assert "valid=1 mode=check" in output.getvalue()


@pytest.mark.django_db
def test_initial_profile_preflights_redirect_before_database_changes(tmp_path):
    profile = write_profile(
        tmp_path,
        """[rfid]
pre_register = ["NO-PARTIAL-CARD"]

[ocpp_redirect]
charger_ip = "192.0.2.10"
targets = ["198.51.100.11"]
""",
    )
    failed = SimpleNamespace(returncode=1, stdout="", stderr="Operation not permitted")

    with patch("apps.imager.initial_profile._run_nft", return_value=failed):
        with pytest.raises(CommandError, match="nft validation"):
            call_command("imager", "initial-profile", "--apply", "--profile", profile)

    assert not RFID.objects.filter(rfid="NO-PARTIAL-CARD").exists()


@pytest.mark.django_db
def test_initial_profile_restores_redirect_when_database_reconciliation_fails(tmp_path):
    Charger.objects.create(charger_id="EVR-RESTORE", auto_start_id_tag="OTHER")
    profile = write_profile(
        tmp_path,
        """[rfid]
pre_register = ["NO-REDIRECT-DRIFT"]

[charger]
id = "EVR-RESTORE"
path = "/ocpp-j/EVR-RESTORE"

[auto_start]
id_tag = "TALLER"

[ocpp_redirect]
charger_ip = "192.0.2.10"
targets = ["198.51.100.11"]
""",
    )
    snapshot = RedirectSnapshot("", None, None, None, None, False)

    with (
        patch("apps.imager.initial_profile._validate_redirect") as validate,
        patch(
            "apps.imager.initial_profile._apply_redirect", return_value=snapshot
        ) as apply,
        patch("apps.imager.initial_profile._restore_redirect") as restore,
        pytest.raises(
            CommandError, match="conflicts with existing charger configuration"
        ),
    ):
        call_command("imager", "initial-profile", "--apply", "--profile", profile)

    assert not RFID.objects.filter(rfid="NO-REDIRECT-DRIFT").exists()
    validate.assert_called_once()
    apply.assert_called_once()
    restore.assert_called_once_with(apply.call_args.args[0], snapshot)


def test_apply_redirect_restores_snapshot_when_installation_fails(tmp_path):
    profile = load_initial_profile(
        write_profile(
            tmp_path,
            """[rfid]
pre_register = []

[ocpp_redirect]
charger_ip = "192.0.2.10"
targets = ["198.51.100.11"]
""",
        )
    )
    assert profile.redirect is not None
    snapshot = RedirectSnapshot("", None, None, None, None, False)
    succeeded = SimpleNamespace(returncode=0, stdout="", stderr="")
    failed = SimpleNamespace(returncode=1, stdout="", stderr="apply failed")

    with (
        patch("apps.imager.initial_profile._snapshot_redirect", return_value=snapshot),
        patch("apps.imager.initial_profile._run_nft", side_effect=[succeeded, failed]),
        patch("apps.imager.initial_profile._restore_redirect") as restore,
        pytest.raises(InitialProfileError, match="could not be applied"),
    ):
        _apply_redirect(profile.redirect)

    restore.assert_called_once_with(profile.redirect, snapshot)
