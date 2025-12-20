import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.test import TestCase
from django.urls import reverse

from apps.nodes.models import Node


class RfidPeerSyncSignatureTests(TestCase):
    def _build_body(self, payload):
        return json.dumps(payload).encode("utf-8")

    def _post_json(self, url, payload, signature=None):
        body = self._build_body(payload)
        headers = {}
        if signature is not None:
            headers["HTTP_X_SIGNATURE"] = signature
        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            **headers,
        )
        return response, body

    def test_missing_signature_rejected(self):
        payload = {"requester": "missing-sig"}
        for url in (reverse("rfid-export"), reverse("rfid-import")):
            with self.subTest(url=url):
                response, _body = self._post_json(url, payload)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json(), {"detail": "signature required"})

    def test_invalid_signature_rejected(self):
        payload = {"requester": "invalid-sig"}
        for url in (reverse("rfid-export"), reverse("rfid-import")):
            with self.subTest(url=url):
                response, _body = self._post_json(url, payload, signature="not-base64!!")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json(), {"detail": "invalid signature"})

    def test_unknown_requester_rejected(self):
        payload = {"requester": "00000000-0000-0000-0000-000000000000"}
        signature = base64.b64encode(b"signature").decode("utf-8")
        for url in (reverse("rfid-export"), reverse("rfid-import")):
            with self.subTest(url=url):
                response, _body = self._post_json(url, payload, signature=signature)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json(), {"detail": "unknown requester"})

    def test_signed_export_returns_rfids(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        node = Node.objects.create(hostname="peer-node", public_key=public_pem)

        payload = {"requester": str(node.uuid)}
        body = self._build_body(payload)
        signature = base64.b64encode(
            private_key.sign(
                body,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        ).decode("utf-8")
        response = self.client.post(
            reverse("rfid-export"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("rfids", data)
        self.assertIsInstance(data["rfids"], list)

    def test_signed_import_returns_summary(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        node = Node.objects.create(hostname="peer-node", public_key=public_pem)

        payload = {"requester": str(node.uuid), "rfids": []}
        body = self._build_body(payload)
        signature = base64.b64encode(
            private_key.sign(
                body,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        ).decode("utf-8")
        response = self.client.post(
            reverse("rfid-import"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            sorted(data.keys()),
            [
                "accounts_linked",
                "created",
                "errors",
                "missing_accounts",
                "processed",
                "updated",
            ],
        )
