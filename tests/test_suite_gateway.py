import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from arthexis import suite as suite_module
class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code == 200
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class SuiteGatewayTests(TestCase):
    def setUp(self):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    def _mock_session(self, responses):
        mock_session = MagicMock()
        mock_session.post.side_effect = responses
        return mock_session

    @patch("arthexis.suite.serialization.load_pem_private_key")
    @patch("arthexis.suite.requests.Session")
    def test_connect_registers_interface_node(self, mock_session_cls, mock_load_key):
        uuid_value = "1234-interface"
        responses = [DummyResponse({"id": 1, "uuid": uuid_value})]
        mock_session = self._mock_session(responses)
        mock_session_cls.return_value = mock_session
        mock_load_key.return_value = self.private_key

        gateway = suite_module.SuiteGateway()
        gateway.connect("example.com", "admin", "secret", self.private_pem)

        self.assertTrue(gateway.connected)
        body = json.loads(mock_session.post.call_args[1]["data"])
        self.assertEqual(body["role"], suite_module.INTERFACE_ROLE)
        self.assertIn("token", body)
        self.assertEqual(gateway._node_uuid, uuid_value)

    @patch("arthexis.suite.serialization.load_pem_private_key")
    @patch("arthexis.suite.requests.Session")
    def test_objects_returns_remote_records(self, mock_session_cls, mock_load_key):
        uuid_value = "abcd"
        responses = [
            DummyResponse({"id": 1, "uuid": uuid_value}),
            DummyResponse(
                {
                    "models": [
                        {
                            "app_label": "nodes",
                            "model": "node",
                            "object_name": "Node",
                            "suite_name": "Nodes",
                        }
                    ]
                }
            ),
            DummyResponse(
                {
                    "objects": [
                        {
                            "model": "nodes.node",
                            "pk": 1,
                            "fields": {"hostname": "remote"},
                        }
                    ]
                }
            ),
        ]
        mock_session = self._mock_session(responses)
        mock_session_cls.return_value = mock_session
        mock_load_key.return_value = self.private_key

        gateway = suite_module.SuiteGateway()
        gateway.connect("example.com", "admin", "secret", self.private_pem)
        records = gateway.Nodes.objects(hostname="remote")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].hostname, "remote")

    @patch("arthexis.suite.serialization.load_pem_private_key")
    @patch("arthexis.suite.requests.Session")
    def test_get_raises_when_object_missing(self, mock_session_cls, mock_load_key):
        uuid_value = "abcd"
        responses = [
            DummyResponse({"id": 1, "uuid": uuid_value}),
            DummyResponse(
                {
                    "models": [
                        {
                            "app_label": "nodes",
                            "model": "node",
                            "object_name": "Node",
                            "suite_name": "Nodes",
                        }
                    ]
                }
            ),
            DummyResponse({"object": None}),
        ]
        mock_session = self._mock_session(responses)
        mock_session_cls.return_value = mock_session
        mock_load_key.return_value = self.private_key

        gateway = suite_module.SuiteGateway()
        gateway.connect("example.com", "admin", "secret", self.private_pem)
        with self.assertRaises(suite_module.SuiteError):
            gateway.Nodes.get(pk=99)
