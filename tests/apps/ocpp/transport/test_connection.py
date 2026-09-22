from unittest import TestCase

from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.transport.connection import (
    ConnectionRejected,
    basic_credentials,
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
