import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.hashers import make_password
from django.test import override_settings

from apps.ocpp.models import Charger, OcppPolicy
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.transport.connection import (
    basic_credentials,
    load_or_enroll_charger,
    negotiate_subprotocol,
)


def test_negotiation_prefers_an_offered_retained_version() -> None:
    assert negotiate_subprotocol(["ocpp2.0.1"]) == (
        "ocpp2.0.1",
        ProtocolVersion.OCPP_201,
    )


@pytest.mark.parametrize("offered", [[], ["ocpp1.5"], ["vendor-protocol"]])
def test_negotiation_falls_back_to_ocpp_16j_when_protocol_is_missing_or_unknown(
    offered,
) -> None:
    assert negotiate_subprotocol(offered) == ("ocpp1.6", ProtocolVersion.OCPP_16)


def test_basic_credentials_are_parsed_without_retaining_headers() -> None:
    assert basic_credentials(
        [(b"authorization", b"Basic Y2hhcmdlcjE6c2VjcmV0")]
    ) == ("charger1", "secret")


@pytest.mark.django_db
def test_open_admission_enrolls_unknown_charger_without_credentials() -> None:
    selected = async_to_sync(load_or_enroll_charger)("charger-new", None)

    assert selected is not None
    assert selected.identity == "charger-new"
    assert selected.enrolled_at is not None
    assert selected.authority_cutover_at == selected.enrolled_at
    assert selected.connection_token_hash == ""
    assert selected.authorization_mode == Charger.AuthorizationMode.OPEN
    assert OcppPolicy.load().charger_admission_mode == OcppPolicy.AdmissionMode.OPEN


@pytest.mark.django_db
def test_open_admission_accepts_existing_active_charger_without_credentials() -> None:
    existing = Charger.objects.create(identity="charger-existing")

    selected = async_to_sync(load_or_enroll_charger)("charger-existing", None)

    assert selected == existing


@pytest.mark.django_db
def test_open_admission_still_rejects_disabled_chargers() -> None:
    Charger.objects.create(identity="charger-disabled", active=False)

    selected = async_to_sync(load_or_enroll_charger)("charger-disabled", None)

    assert selected is None


@pytest.mark.django_db
def test_trusted_listener_overrides_restricted_instance_admission() -> None:
    policy = OcppPolicy.load()
    policy.charger_admission_mode = OcppPolicy.AdmissionMode.RESTRICTED
    policy.save()

    selected = async_to_sync(load_or_enroll_charger)(
        "charger-local",
        None,
        trusted_listener=True,
    )

    assert selected is not None
    assert selected.identity == "charger-local"
    assert selected.connection_token_hash == ""


@pytest.mark.django_db
def test_untrusted_listener_obeys_restricted_instance_admission() -> None:
    policy = OcppPolicy.load()
    policy.charger_admission_mode = OcppPolicy.AdmissionMode.RESTRICTED
    policy.save()

    selected = async_to_sync(load_or_enroll_charger)(
        "charger-remote",
        None,
        trusted_listener=False,
    )

    assert selected is None
    assert not Charger.objects.filter(identity="charger-remote").exists()


@pytest.mark.django_db
def test_restricted_admission_requires_enrollment_credentials_for_unknown_charger() -> None:
    policy = OcppPolicy.load()
    policy.charger_admission_mode = OcppPolicy.AdmissionMode.RESTRICTED
    policy.save()

    with override_settings(
        PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    ):
        enrollment_hash = make_password("enroll")
        with override_settings(OCPP_ENROLLMENT_TOKEN_HASH=enrollment_hash):
            enrolled = async_to_sync(load_or_enroll_charger)(
                "charger-new", ("charger-new", "enroll")
            )
            rejected = async_to_sync(load_or_enroll_charger)(
                "charger-rejected", ("charger-rejected", "wrong")
            )

    assert enrolled is not None
    assert enrolled.connection_token_hash
    assert rejected is None
    assert not Charger.objects.filter(identity="charger-rejected").exists()


@pytest.mark.django_db
def test_restricted_admission_authenticates_existing_chargers() -> None:
    policy = OcppPolicy.load()
    policy.charger_admission_mode = OcppPolicy.AdmissionMode.RESTRICTED
    policy.save()
    existing = Charger.objects.create(
        identity="charger-existing",
        connection_token_hash=make_password("secret"),
    )

    accepted = async_to_sync(load_or_enroll_charger)(
        "charger-existing", ("charger-existing", "secret")
    )
    rejected = async_to_sync(load_or_enroll_charger)(
        "charger-existing", ("charger-existing", "wrong")
    )

    assert accepted == existing
    assert rejected is None
