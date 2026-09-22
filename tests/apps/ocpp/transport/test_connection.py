from asgiref.sync import async_to_sync
from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings

from apps.ocpp.models import Charger
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.transport.connection import (
    ConnectionRejected,
    basic_credentials,
    load_or_enroll_charger,
    negotiate_subprotocol,
)


class ConnectionTests(TestCase):
    def test_negotiation_requires_an_offered_retained_version(self) -> None:
        self.assertEqual(
            negotiate_subprotocol(["ocpp2.0.1"]),
            ("ocpp2.0.1", ProtocolVersion.OCPP_201),
        )
        with self.assertRaises(ConnectionRejected):
            negotiate_subprotocol(["ocpp1.5"])

    def test_basic_credentials_are_parsed_without_retaining_headers(self) -> None:
        self.assertEqual(
            basic_credentials([(b"authorization", b"Basic Y2hhcmdlcjE6c2VjcmV0")]),
            ("charger1", "secret"),
        )


class ChargerEnrollmentTests(TestCase):
    def test_unknown_identity_requires_a_valid_enrollment_credential(self) -> None:
        with override_settings(OCPP_ENROLLMENT_TOKEN_HASH=make_password("enroll")):
            enrolled = async_to_sync(load_or_enroll_charger)(
                "charger-new", ("charger-new", "enroll")
            )
            rejected = async_to_sync(load_or_enroll_charger)(
                "charger-rejected", ("charger-rejected", "wrong")
            )

        self.assertIsNotNone(enrolled)
        self.assertEqual(enrolled.identity, "charger-new")
        self.assertIsNotNone(enrolled.enrolled_at)
        self.assertEqual(enrolled.authority_cutover_at, enrolled.enrolled_at)
        self.assertTrue(enrolled.active)
        self.assertEqual(enrolled.authorization_mode, Charger.AuthorizationMode.OPEN)
        self.assertFalse(Charger.objects.filter(identity="charger-rejected").exists())

    def test_enrollment_is_disabled_without_a_configured_credential(self) -> None:
        with override_settings(OCPP_ENROLLMENT_TOKEN_HASH=""):
            selected = async_to_sync(load_or_enroll_charger)(
                "charger-new", ("charger-new", "enroll")
            )

        self.assertIsNone(selected)
        self.assertFalse(Charger.objects.filter(identity="charger-new").exists())
