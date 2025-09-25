import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, call, MagicMock
from django.core import mail
from django.core.mail import EmailMessage
from django.core.management import call_command
import socket
import base64
import json
import uuid
from tempfile import TemporaryDirectory
import shutil
import stat
import time
from datetime import timedelta

from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib import admin
from django.contrib.sites.models import Site
from django_celery_beat.models import PeriodicTask
from django.conf import settings
from django.utils import timezone
from .actions import NodeAction
from selenium.common.exceptions import WebDriverException
from .utils import capture_screenshot

from .models import (
    Node,
    EmailOutbox,
    ContentSample,
    NodeRole,
    NodeFeature,
    NodeFeatureAssignment,
    NetMessage,
)
from .backends import OutboxEmailBackend
from .tasks import capture_node_screenshot, sample_clipboard
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from core.models import PackageRelease, SecurityGroup


class NodeTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="nodeuser", password="pwd")
        self.client.force_login(self.user)
        NodeRole.objects.get_or_create(name="Terminal")

    def test_register_current_does_not_create_release(self):
        node = None
        created = False
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:ff:ee:dd:cc:bb",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="testhost"),
                    patch(
                        "nodes.models.socket.gethostbyname", return_value="127.0.0.1"
                    ),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                ):
                    node, created = Node.register_current()
        self.assertEqual(PackageRelease.objects.count(), 0)
        self.assertIsNotNone(node)
        self.assertTrue(created)
        self.assertEqual(node.current_relation, Node.Relation.SELF)

    def test_register_and_list_node(self):
        response = self.client.post(
            reverse("register-node"),
            data={
                "hostname": "local",
                "address": "127.0.0.1",
                "port": 8000,
                "mac_address": "00:11:22:33:44:55",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Node.objects.count(), 1)
        node = Node.objects.get(mac_address="00:11:22:33:44:55")
        self.assertEqual(node.current_relation, Node.Relation.PEER)

        # allow same IP with different MAC
        self.client.post(
            reverse("register-node"),
            data={
                "hostname": "local2",
                "address": "127.0.0.1",
                "port": 8001,
                "mac_address": "00:11:22:33:44:66",
            },
            content_type="application/json",
        )
        self.assertEqual(Node.objects.count(), 2)

        # duplicate MAC should not create new node
        dup = self.client.post(
            reverse("register-node"),
            data={
                "hostname": "dup",
                "address": "127.0.0.2",
                "port": 8002,
                "mac_address": "00:11:22:33:44:55",
            },
            content_type="application/json",
        )
        self.assertEqual(Node.objects.count(), 2)
        self.assertIn("already exists", dup.json()["detail"])
        self.assertEqual(dup.json()["id"], response.json()["id"])

        list_resp = self.client.get(reverse("node-list"))
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertEqual(len(data["nodes"]), 2)
        hostnames = {n["hostname"] for n in data["nodes"]}
        self.assertEqual(hostnames, {"dup", "local2"})

    def test_register_node_feature_toggle(self):
        NodeFeature.objects.get_or_create(
            slug="clipboard-poll", defaults={"display": "Clipboard Poll"}
        )
        url = reverse("register-node")
        first = self.client.post(
            url,
            data={
                "hostname": "lcd",
                "address": "127.0.0.1",
                "port": 8000,
                "mac_address": "00:aa:bb:cc:dd:ee",
                "features": ["clipboard-poll"],
            },
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)
        node = Node.objects.get(mac_address="00:aa:bb:cc:dd:ee")
        self.assertTrue(node.has_feature("clipboard-poll"))

        self.client.post(
            url,
            data={
                "hostname": "lcd",
                "address": "127.0.0.1",
                "port": 8000,
                "mac_address": "00:aa:bb:cc:dd:ee",
                "features": [],
            },
            content_type="application/json",
        )
        node.refresh_from_db()
        self.assertFalse(node.has_feature("clipboard-poll"))

    def test_register_node_records_version_details(self):
        url = reverse("register-node")
        payload = {
            "hostname": "versioned",
            "address": "127.0.0.5",
            "port": 8100,
            "mac_address": "aa:bb:cc:dd:ee:10",
            "installed_version": "2.0.1",
            "installed_revision": "rev-abcdef",
        }
        response = self.client.post(
            url, data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        node = Node.objects.get(mac_address="aa:bb:cc:dd:ee:10")
        self.assertEqual(node.installed_version, "2.0.1")
        self.assertEqual(node.installed_revision, "rev-abcdef")

        update_payload = {
            **payload,
            "installed_version": "2.1.0",
            "installed_revision": "rev-fedcba",
        }
        second = self.client.post(
            url, data=json.dumps(update_payload), content_type="application/json"
        )
        self.assertEqual(second.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(node.installed_version, "2.1.0")
        self.assertEqual(node.installed_revision, "rev-fedcba")

    def test_register_node_update_triggers_notification(self):
        node = Node.objects.create(
            hostname="friend",
            address="10.1.1.5",
            port=8123,
            mac_address="aa:bb:cc:dd:ee:01",
            installed_version="1.0.0",
            installed_revision="rev-old",
        )
        url = reverse("register-node")
        payload = {
            "hostname": "friend",
            "address": "10.1.1.5",
            "port": 8123,
            "mac_address": "aa:bb:cc:dd:ee:01",
            "installed_version": "2.0.0",
            "installed_revision": "abcdef123456",
        }
        with patch("nodes.models.notify_async") as mock_notify:
            response = self.client.post(
                url, data=json.dumps(payload), content_type="application/json"
            )
        self.assertEqual(response.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(node.installed_version, "2.0.0")
        self.assertEqual(node.installed_revision, "abcdef123456")
        mock_notify.assert_called_once()
        subject, body = mock_notify.call_args[0]
        self.assertEqual(subject, "UP friend")
        self.assertEqual(body, "v2.0.0 r123456")

    def test_register_node_update_without_version_change_still_notifies(self):
        node = Node.objects.create(
            hostname="friend",
            address="10.1.1.5",
            port=8123,
            mac_address="aa:bb:cc:dd:ee:02",
            installed_version="2.0.0",
            installed_revision="abcdef123456",
        )
        url = reverse("register-node")
        payload = {
            "hostname": "friend",
            "address": "10.1.1.5",
            "port": 8123,
            "mac_address": "aa:bb:cc:dd:ee:02",
            "installed_version": "2.0.0",
            "installed_revision": "abcdef123456",
        }
        with patch("nodes.models.notify_async") as mock_notify:
            response = self.client.post(
                url, data=json.dumps(payload), content_type="application/json"
            )
        self.assertEqual(response.status_code, 200)
        node.refresh_from_db()
        mock_notify.assert_called_once()
        subject, body = mock_notify.call_args[0]
        self.assertEqual(subject, "UP friend")
        self.assertEqual(body, "v2.0.0 r123456")

    def test_register_node_creation_triggers_notification(self):
        url = reverse("register-node")
        payload = {
            "hostname": "newbie",
            "address": "10.1.1.6",
            "port": 8124,
            "mac_address": "aa:bb:cc:dd:ee:03",
            "installed_version": "3.0.0",
            "installed_revision": "rev-1234567890",
        }
        with patch("nodes.models.notify_async") as mock_notify:
            response = self.client.post(
                url, data=json.dumps(payload), content_type="application/json"
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Node.objects.filter(mac_address="aa:bb:cc:dd:ee:03").exists())
        mock_notify.assert_called_once()
        subject, body = mock_notify.call_args[0]
        self.assertEqual(subject, "UP newbie")
        self.assertEqual(body, "v3.0.0 r567890")

    def test_register_node_sets_cors_headers(self):
        payload = {
            "hostname": "cors",
            "address": "127.0.0.1",
            "port": 8000,
            "mac_address": "10:20:30:40:50:60",
        }
        response = self.client.post(
            reverse("register-node"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_ORIGIN="http://example.com",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://example.com")
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")

    def test_register_node_requires_auth_without_signature(self):
        self.client.logout()
        payload = {
            "hostname": "visitor",
            "address": "127.0.0.1",
            "port": 8000,
            "mac_address": "aa:bb:cc:dd:ee:00",
        }
        response = self.client.post(
            reverse("register-node"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_ORIGIN="http://example.com",
        )
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data["detail"], "authentication required")
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://example.com")

    def test_register_node_allows_preflight_without_authentication(self):
        self.client.logout()
        response = self.client.options(
            reverse("register-node"), HTTP_ORIGIN="https://example.com"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://example.com")
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")

    def test_register_node_accepts_signed_payload_without_login(self):
        self.client.logout()
        NodeFeature.objects.get_or_create(
            slug="clipboard-poll", defaults={"display": "Clipboard Poll"}
        )
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        token = "visitor-token"
        signature = base64.b64encode(
            private_key.sign(
                token.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        ).decode()
        payload = {
            "hostname": "visitor",
            "address": "127.0.0.1",
            "port": 8000,
            "mac_address": "aa:bb:cc:dd:ee:11",
            "public_key": public_bytes,
            "token": token,
            "signature": signature,
            "features": ["clipboard-poll"],
        }
        response = self.client.post(
            reverse("register-node"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_ORIGIN="http://example.com",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://example.com")
        node = Node.objects.get(mac_address="aa:bb:cc:dd:ee:11")
        self.assertEqual(node.public_key, public_bytes)
        self.assertTrue(node.has_feature("clipboard-poll"))

    def test_register_node_accepts_text_plain_payload(self):
        payload = {
            "hostname": "plain",
            "address": "127.0.0.1",
            "port": 8001,
            "mac_address": "aa:bb:cc:dd:ee:ff",
        }
        response = self.client.post(
            reverse("register-node"),
            data=json.dumps(payload),
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Node.objects.filter(mac_address="aa:bb:cc:dd:ee:ff").exists())

    def test_register_node_respects_relation_payload(self):
        payload = {
            "hostname": "relation",
            "address": "127.0.0.2",
            "port": 8100,
            "mac_address": "11:22:33:44:55:66",
            "current_relation": "Downstream",
        }
        response = self.client.post(
            reverse("register-node"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        node = Node.objects.get(mac_address="11:22:33:44:55:66")
        self.assertEqual(node.current_relation, Node.Relation.DOWNSTREAM)

        update_payload = {
            **payload,
            "hostname": "relation-updated",
            "current_relation": "Upstream",
        }
        second = self.client.post(
            reverse("register-node"),
            data=json.dumps(update_payload),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(node.current_relation, Node.Relation.UPSTREAM)

        final_payload = {**update_payload, "hostname": "relation-final"}
        final_payload.pop("current_relation")
        third = self.client.post(
            reverse("register-node"),
            data=json.dumps(final_payload),
            content_type="application/json",
        )
        self.assertEqual(third.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(node.current_relation, Node.Relation.UPSTREAM)


class NodeRegisterCurrentTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()
        self.user = User.objects.create_user(username="nodeuser", password="pwd")
        self.client.force_login(self.user)
        NodeRole.objects.get_or_create(name="Terminal")

    def test_register_current_notifies_peers_on_start(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:ff:ee:dd:cc:bb",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="testhost"),
                    patch(
                        "nodes.models.socket.gethostbyname", return_value="127.0.0.1"
                    ),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                    patch.object(Node, "notify_peers_of_update") as mock_notify,
                ):
                    Node.register_current()
        mock_notify.assert_called_once()

    def test_register_current_refreshes_lcd_feature(self):
        NodeFeature.objects.get_or_create(
            slug="lcd-screen", defaults={"display": "LCD Screen"}
        )
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            locks.mkdir()
            lock = locks / "lcd_screen.lck"
            lock.touch()
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:ff:ee:dd:cc:bb",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="testhost"),
                    patch(
                        "nodes.models.socket.gethostbyname", return_value="127.0.0.1"
                    ),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                ):
                    node, created = Node.register_current()
            self.assertTrue(created)
            self.assertTrue(node.has_feature("lcd-screen"))

            lock.unlink()
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:ff:ee:dd:cc:bb",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="testhost"),
                    patch(
                        "nodes.models.socket.gethostbyname", return_value="127.0.0.1"
                    ),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                ):
                    _, created2 = Node.register_current()
            self.assertFalse(created2)
            node.refresh_from_db()
            self.assertFalse(node.has_feature("lcd-screen"))

            lock.touch()
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:ff:ee:dd:cc:bb",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="testhost"),
                    patch(
                        "nodes.models.socket.gethostbyname", return_value="127.0.0.1"
                    ),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                ):
                    node, created3 = Node.register_current()
            self.assertFalse(created3)
            node.refresh_from_db()
            self.assertTrue(node.has_feature("lcd-screen"))

    def test_register_current_notifies_peers_on_version_upgrade(self):
        remote = Node.objects.create(
            hostname="remote",
            address="10.0.0.2",
            port=9100,
            mac_address="aa:bb:cc:dd:ee:ff",
        )
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "VERSION").write_text("2.0.0")
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:ff:ee:dd:cc:bb",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="localnode"),
                    patch(
                        "nodes.models.socket.gethostbyname",
                        return_value="192.168.1.5",
                    ),
                    patch("nodes.models.revision.get_revision", return_value="newrev"),
                    patch("requests.post") as mock_post,
                ):
                    Node.objects.create(
                        hostname="localnode",
                        address="192.168.1.5",
                        port=8000,
                        mac_address="00:ff:ee:dd:cc:bb",
                        installed_version="1.9.0",
                        installed_revision="oldrev",
                    )
                    mock_post.return_value = SimpleNamespace(
                        ok=True, status_code=200, text=""
                    )
                    node, created = Node.register_current()
        self.assertFalse(created)
        self.assertGreaterEqual(mock_post.call_count, 1)
        args, kwargs = mock_post.call_args
        self.assertIn(str(remote.port), args[0])
        payload = json.loads(kwargs["data"])
        self.assertEqual(payload["hostname"], "localnode")
        self.assertEqual(payload["installed_version"], "2.0.0")
        self.assertEqual(payload["installed_revision"], "newrev")

    def test_register_current_notifies_peers_without_version_change(self):
        Node.objects.create(
            hostname="remote",
            address="10.0.0.3",
            port=9200,
            mac_address="aa:bb:cc:dd:ee:11",
        )
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "VERSION").write_text("1.0.0")
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:ff:ee:dd:cc:cc",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="samever"),
                    patch(
                        "nodes.models.socket.gethostbyname",
                        return_value="192.168.1.6",
                    ),
                    patch("nodes.models.revision.get_revision", return_value="rev1"),
                    patch("requests.post") as mock_post,
                ):
                    Node.objects.create(
                        hostname="samever",
                        address="192.168.1.6",
                        port=8000,
                        mac_address="00:ff:ee:dd:cc:cc",
                        installed_version="1.0.0",
                        installed_revision="rev1",
                    )
                    mock_post.return_value = SimpleNamespace(
                        ok=True, status_code=200, text=""
                    )
                    Node.register_current()
        self.assertEqual(mock_post.call_count, 1)
        args, kwargs = mock_post.call_args
        self.assertIn("/nodes/register/", args[0])
        payload = json.loads(kwargs["data"])
        self.assertEqual(payload["installed_version"], "1.0.0")
        self.assertEqual(payload.get("installed_revision"), "rev1")

    @patch("nodes.views.capture_screenshot")
    def test_capture_screenshot(self, mock_capture):
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        file_path.write_bytes(b"test")
        mock_capture.return_value = Path("screenshots/test.png")
        response = self.client.get(reverse("node-screenshot"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["screenshot"], "screenshots/test.png")
        self.assertEqual(data["node"], node.id)
        mock_capture.assert_called_once()
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 1
        )
        screenshot = ContentSample.objects.filter(kind=ContentSample.IMAGE).first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.method, "GET")

    @patch("nodes.views.capture_screenshot")
    def test_duplicate_screenshot_skipped(self, mock_capture):
        hostname = socket.gethostname()
        Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "dup.png"
        file_path.write_bytes(b"dup")
        mock_capture.return_value = Path("screenshots/dup.png")
        self.client.get(reverse("node-screenshot"))
        self.client.get(reverse("node-screenshot"))
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 1
        )

    @patch("nodes.views.capture_screenshot")
    def test_capture_screenshot_error(self, mock_capture):
        hostname = socket.gethostname()
        Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )
        mock_capture.side_effect = RuntimeError("fail")
        response = self.client.get(reverse("node-screenshot"))
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertEqual(data["detail"], "fail")
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 0
        )

    def test_public_api_get_and_post(self):
        node = Node.objects.create(
            hostname="public",
            address="127.0.0.1",
            port=8001,
            enable_public_api=True,
            mac_address="00:11:22:33:44:77",
        )
        url = reverse("node-public-endpoint", args=[node.public_endpoint])

        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["hostname"], "public")

        pre_count = NetMessage.objects.count()
        post_resp = self.client.post(url, data="hello", content_type="text/plain")
        self.assertEqual(post_resp.status_code, 200)
        self.assertEqual(NetMessage.objects.count(), pre_count + 1)
        msg = NetMessage.objects.order_by("-created").first()
        self.assertEqual(msg.body, "hello")
        self.assertEqual(msg.reach.name, "Terminal")

    def test_public_api_disabled(self):
        node = Node.objects.create(
            hostname="nopublic",
            address="127.0.0.2",
            port=8002,
            mac_address="00:11:22:33:44:88",
        )
        url = reverse("node-public-endpoint", args=[node.public_endpoint])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_net_message_requires_signature(self):
        payload = {
            "uuid": str(uuid.uuid4()),
            "subject": "s",
            "body": "b",
            "seen": [],
            "sender": str(uuid.uuid4()),
        }
        resp = self.client.post(
            reverse("net-message"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_net_message_with_valid_signature(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = (
            key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        sender = Node.objects.create(
            hostname="sender",
            address="10.0.0.1",
            port=8000,
            mac_address="00:11:22:33:44:cc",
            public_key=public_key,
        )
        msg_id = str(uuid.uuid4())
        payload = {
            "uuid": msg_id,
            "subject": "hello",
            "body": "world",
            "seen": [],
            "sender": str(sender.uuid),
            "origin": str(sender.uuid),
        }
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = key.sign(payload_json.encode(), padding.PKCS1v15(), hashes.SHA256())
        resp = self.client.post(
            reverse("net-message"),
            data=payload_json,
            content_type="application/json",
            HTTP_X_SIGNATURE=base64.b64encode(signature).decode(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(NetMessage.objects.filter(uuid=msg_id).exists())
        message = NetMessage.objects.get(uuid=msg_id)
        self.assertEqual(message.node_origin, sender)

    def test_clipboard_polling_creates_task(self):
        feature, _ = NodeFeature.objects.get_or_create(
            slug="clipboard-poll", defaults={"display": "Clipboard Poll"}
        )
        node = Node.objects.create(
            hostname="clip",
            address="127.0.0.1",
            port=9000,
            mac_address="00:11:22:33:44:99",
        )
        task_name = f"poll_clipboard_node_{node.pk}"
        PeriodicTask.objects.filter(name=task_name).delete()
        NodeFeatureAssignment.objects.create(node=node, feature=feature)
        self.assertTrue(PeriodicTask.objects.filter(name=task_name).exists())
        NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())

    def test_screenshot_polling_creates_task(self):
        feature, _ = NodeFeature.objects.get_or_create(
            slug="screenshot-poll", defaults={"display": "Screenshot Poll"}
        )
        node = Node.objects.create(
            hostname="shot",
            address="127.0.0.1",
            port=9100,
            mac_address="00:11:22:33:44:aa",
        )
        task_name = f"capture_screenshot_node_{node.pk}"
        PeriodicTask.objects.filter(name=task_name).delete()
        NodeFeatureAssignment.objects.create(node=node, feature=feature)
        self.assertTrue(PeriodicTask.objects.filter(name=task_name).exists())
        NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())


class CheckRegistrationReadyCommandTests(TestCase):
    def test_command_completes_successfully(self):
        NodeRole.objects.get_or_create(name="Terminal")
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            with override_settings(BASE_DIR=base):
                call_command("check_registration_ready")


class NodeAdminTests(TestCase):

    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="nodes-admin", password="adminpass", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def tearDown(self):
        security_dir = Path(settings.BASE_DIR) / "security"
        if security_dir.exists():
            shutil.rmtree(security_dir)

    def test_register_current_host(self):
        url = reverse("admin:nodes_node_register_current")
        hostname = socket.gethostname()
        with patch("utils.revision.get_revision", return_value="abcdef123456"):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/nodes/node/register_remote.html")
        self.assertEqual(Node.objects.count(), 1)
        node = Node.objects.first()
        ver = Path("VERSION").read_text().strip()
        rev = "abcdef123456"
        self.assertEqual(node.base_path, str(settings.BASE_DIR))
        self.assertEqual(node.installed_version, ver)
        self.assertEqual(node.installed_revision, rev)
        self.assertEqual(node.mac_address, Node.get_current_mac())
        sec_dir = Path(settings.BASE_DIR) / "security"
        priv = sec_dir / f"{node.public_endpoint}"
        pub = sec_dir / f"{node.public_endpoint}.pub"
        self.assertTrue(sec_dir.exists())
        self.assertTrue(priv.exists())
        self.assertTrue(pub.exists())
        self.assertTrue(node.public_key)
        self.assertTrue(Site.objects.filter(domain=hostname, name="host").exists())

    def test_register_current_updates_existing_node(self):
        hostname = socket.gethostname()
        Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=8000,
            mac_address=None,
        )

        response = self.client.get(
            reverse("admin:nodes_node_register_current"), follow=False
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Node.objects.count(), 1)
        node = Node.objects.first()
        self.assertEqual(node.mac_address, Node.get_current_mac())
        self.assertEqual(node.hostname, hostname)

    def test_public_key_download_link(self):
        self.client.get(reverse("admin:nodes_node_register_current"))
        node = Node.objects.first()
        change_url = reverse("admin:nodes_node_change", args=[node.pk])
        response = self.client.get(change_url)
        download_url = reverse("admin:nodes_node_public_key", args=[node.pk])
        self.assertContains(response, download_url)
        resp = self.client.get(download_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp["Content-Disposition"],
            f'attachment; filename="{node.public_endpoint}.pub"',
        )
        self.assertIn(node.public_key.strip(), resp.content.decode())

    @patch("nodes.admin.capture_screenshot")
    def test_capture_site_screenshot_from_admin(self, mock_capture_screenshot):
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        file_path.write_bytes(b"admin")
        mock_capture_screenshot.return_value = Path("screenshots/test.png")
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )
        url = reverse("admin:nodes_contentsample_capture")
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 1
        )
        screenshot = ContentSample.objects.filter(kind=ContentSample.IMAGE).first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.path, "screenshots/test.png")
        self.assertEqual(screenshot.method, "ADMIN")
        mock_capture_screenshot.assert_called_once_with("http://testserver/")
        self.assertContains(response, "Screenshot saved to screenshots/test.png")

    def test_view_screenshot_in_change_admin(self):
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        with file_path.open("wb") as fh:
            fh.write(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR42mP8/5+hHgAFgwJ/lSdX6QAAAABJRU5ErkJggg=="
                )
            )
        screenshot = ContentSample.objects.create(
            path="screenshots/test.png", kind=ContentSample.IMAGE
        )
        url = reverse("admin:nodes_contentsample_change", args=[screenshot.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data:image/png;base64")

    @override_settings(SCREENSHOT_SOURCES=["/one", "/two"])
    @patch("nodes.admin.capture_screenshot")
    def test_take_screenshots_action(self, mock_capture):
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file1 = screenshot_dir / "one.png"
        file1.write_bytes(b"1")
        file2 = screenshot_dir / "two.png"
        file2.write_bytes(b"2")
        mock_capture.side_effect = [
            Path("screenshots/one.png"),
            Path("screenshots/two.png"),
        ]
        node = Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )
        url = reverse("admin:nodes_node_changelist")
        resp = self.client.post(
            url,
            {"action": "take_screenshots", "_selected_action": [str(node.pk)]},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 2
        )
        samples = list(ContentSample.objects.filter(kind=ContentSample.IMAGE))
        self.assertEqual(samples[0].transaction_uuid, samples[1].transaction_uuid)


class NetMessageAdminTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="netmsg-admin", password="adminpass", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        NodeRole.objects.get_or_create(name="Terminal")

    def test_complete_flag_not_editable(self):
        msg = NetMessage.objects.create(subject="s", body="b")
        url = reverse("admin:nodes_netmessage_change", args=[msg.pk])
        data = {"subject": "s2", "body": "b2", "complete": "on", "_save": "Save"}
        self.client.post(url, data)
        msg.refresh_from_db()
        self.assertFalse(msg.complete)
        self.assertEqual(msg.subject, "s2")

    def test_send_action_calls_propagate(self):
        msg = NetMessage.objects.create(subject="s", body="b")
        with patch.object(NetMessage, "propagate") as mock_propagate:
            response = self.client.post(
                reverse("admin:nodes_netmessage_changelist"),
                {"action": "send_messages", "_selected_action": [str(msg.pk)]},
            )
        self.assertEqual(response.status_code, 302)
        mock_propagate.assert_called_once()


class LastNetMessageViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        NodeRole.objects.get_or_create(name="Terminal")

    def test_returns_latest_message(self):
        NetMessage.objects.create(subject="old", body="msg1")
        NetMessage.objects.create(subject="new", body="msg2")
        resp = self.client.get(reverse("last-net-message"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"subject": "new", "body": "msg2"})


class NetMessageReachTests(TestCase):
    def setUp(self):
        self.roles = {}
        for name in ["Terminal", "Control", "Satellite", "Constellation"]:
            self.roles[name], _ = NodeRole.objects.get_or_create(name=name)
        self.nodes = {}
        for idx, name in enumerate(
            ["Terminal", "Control", "Satellite", "Constellation"], start=1
        ):
            self.nodes[name] = Node.objects.create(
                hostname=name.lower(),
                address=f"10.0.0.{idx}",
                port=8000 + idx,
                mac_address=f"00:11:22:33:44:{idx:02x}",
                role=self.roles[name],
            )

    @patch("requests.post")
    def test_terminal_reach_limits_nodes(self, mock_post):
        msg = NetMessage.objects.create(
            subject="s", body="b", reach=self.roles["Terminal"]
        )
        with patch.object(Node, "get_local", return_value=None):
            msg.propagate()
        roles = set(msg.propagated_to.values_list("role__name", flat=True))
        self.assertEqual(roles, {"Terminal"})
        self.assertEqual(mock_post.call_count, 1)

    @patch("requests.post")
    def test_control_reach_includes_control_and_terminal(self, mock_post):
        msg = NetMessage.objects.create(
            subject="s", body="b", reach=self.roles["Control"]
        )
        with patch.object(Node, "get_local", return_value=None):
            msg.propagate()
        roles = set(msg.propagated_to.values_list("role__name", flat=True))
        self.assertEqual(roles, {"Control", "Terminal"})
        self.assertEqual(mock_post.call_count, 2)

    @patch("requests.post")
    def test_satellite_reach_includes_lower_roles(self, mock_post):
        msg = NetMessage.objects.create(
            subject="s", body="b", reach=self.roles["Satellite"]
        )
        with patch.object(Node, "get_local", return_value=None):
            msg.propagate()
        roles = set(msg.propagated_to.values_list("role__name", flat=True))
        self.assertEqual(roles, {"Satellite", "Control", "Terminal"})
        self.assertEqual(mock_post.call_count, 3)

    @patch("requests.post")
    def test_constellation_reach_prioritizes_constellation(self, mock_post):
        msg = NetMessage.objects.create(
            subject="s", body="b", reach=self.roles["Constellation"]
        )
        with patch.object(Node, "get_local", return_value=None):
            msg.propagate()
        roles = set(msg.propagated_to.values_list("role__name", flat=True))
        self.assertEqual(roles, {"Constellation", "Satellite", "Control"})
        self.assertEqual(mock_post.call_count, 3)


class NetMessagePropagationTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.local = Node.objects.create(
            hostname="local",
            address="10.0.0.1",
            port=8001,
            mac_address="00:11:22:33:44:00",
            role=self.role,
            public_endpoint="local",
        )
        self.remotes = []
        for idx in range(2, 6):
            self.remotes.append(
                Node.objects.create(
                    hostname=f"n{idx}",
                    address=f"10.0.0.{idx}",
                    port=8000 + idx,
                    mac_address=f"00:11:22:33:44:{idx:02x}",
                    role=self.role,
                    public_endpoint=f"n{idx}",
                )
            )

    def test_broadcast_sets_node_origin(self):
        with patch.object(Node, "get_local", return_value=self.local):
            msg = NetMessage.broadcast(subject="subject", body="body")
        self.assertEqual(msg.node_origin, self.local)

    @patch("requests.post")
    @patch("core.notifications.notify")
    def test_propagate_forwards_to_three_and_notifies_local(
        self, mock_notify, mock_post
    ):
        msg = NetMessage.objects.create(subject="s", body="b", reach=self.role)
        with patch.object(Node, "get_local", return_value=self.local):
            msg.propagate(seen=[str(self.remotes[0].uuid)])
        mock_notify.assert_called_once_with("s", "b")
        self.assertEqual(mock_post.call_count, 3)
        for call_args in mock_post.call_args_list:
            payload = json.loads(call_args.kwargs["data"])
            self.assertEqual(payload.get("origin"), str(self.local.uuid))
        targets = {
            call.args[0].split("//")[1].split("/")[0]
            for call in mock_post.call_args_list
        }
        sender_addr = f"{self.remotes[0].address}:{self.remotes[0].port}"
        self.assertNotIn(sender_addr, targets)
        self.assertEqual(msg.propagated_to.count(), 4)
        self.assertTrue(msg.complete)

    @patch("requests.post")
    @patch("core.notifications.notify", return_value=True)
    def test_propagate_prunes_old_local_messages(self, mock_notify, mock_post):
        old_local = NetMessage.objects.create(
            subject="old local",
            body="body",
            reach=self.role,
            node_origin=self.local,
        )
        NetMessage.objects.filter(pk=old_local.pk).update(
            created=timezone.now() - timedelta(days=8)
        )
        old_remote = NetMessage.objects.create(
            subject="old remote",
            body="body",
            reach=self.role,
            node_origin=self.remotes[0],
        )
        NetMessage.objects.filter(pk=old_remote.pk).update(
            created=timezone.now() - timedelta(days=8)
        )
        msg = NetMessage.objects.create(
            subject="fresh",
            body="body",
            reach=self.role,
            node_origin=self.local,
        )
        with patch.object(Node, "get_local", return_value=self.local):
            msg.propagate()

        mock_notify.assert_called_once_with("fresh", "body")
        self.assertFalse(NetMessage.objects.filter(pk=old_local.pk).exists())
        self.assertTrue(NetMessage.objects.filter(pk=old_remote.pk).exists())
        self.assertTrue(NetMessage.objects.filter(pk=msg.pk).exists())


class NodeActionTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="action-admin", password="adminpass", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_registry_and_local_execution(self):
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )

        class DummyAction(NodeAction):
            display_name = "Dummy Action"

            def execute(self, node, **kwargs):
                DummyAction.executed = node

        try:
            DummyAction.executed = None
            DummyAction.run()
            self.assertEqual(DummyAction.executed, node)
            self.assertIn("dummyaction", NodeAction.registry)
        finally:
            NodeAction.registry.pop("dummyaction", None)

    def test_remote_not_supported(self):
        node = Node.objects.create(
            hostname="remote",
            address="10.0.0.1",
            port=8000,
            mac_address="00:11:22:33:44:bb",
        )

        class DummyAction(NodeAction):
            def execute(self, node, **kwargs):
                pass

        try:
            with self.assertRaises(NotImplementedError):
                DummyAction.run(node)
        finally:
            NodeAction.registry.pop("dummyaction", None)

    def test_admin_change_view_lists_actions(self):
        hostname = socket.gethostname()
        node = Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )

        class DummyAction(NodeAction):
            display_name = "Dummy Action"

            def execute(self, node, **kwargs):
                pass

        try:
            url = reverse("admin:nodes_node_change", args=[node.pk])
            response = self.client.get(url)
            self.assertContains(response, "Dummy Action")
        finally:
            NodeAction.registry.pop("dummyaction", None)


class StartupNotificationTests(TestCase):
    def test_startup_notification_uses_hostname_and_revision(self):
        from nodes.apps import _startup_notification

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.2.3")
            with self.settings(BASE_DIR=tmp_path):
                with patch(
                    "nodes.apps.revision.get_revision", return_value="abcdef123456"
                ):
                    with patch("nodes.models.NetMessage.broadcast") as mock_broadcast:
                        with patch(
                            "nodes.apps.socket.gethostname", return_value="host"
                        ):
                            with patch.dict(os.environ, {"PORT": "9000"}):
                                _startup_notification()
                                time.sleep(0.1)

        mock_broadcast.assert_called_once()
        _, kwargs = mock_broadcast.call_args
        self.assertEqual(kwargs["subject"], "host:9000")
        self.assertTrue(kwargs["body"].startswith("1.2.3 r"))


class StartupHandlerTests(TestCase):
    def test_handler_logs_db_errors(self):
        from nodes.apps import _trigger_startup_notification
        from django.db.utils import OperationalError

        with patch("nodes.apps._startup_notification") as mock_start:
            with patch("nodes.apps.connections") as mock_connections:
                mock_connections.__getitem__.return_value.ensure_connection.side_effect = OperationalError(
                    "fail"
                )
                with self.assertLogs("nodes.apps", level="ERROR") as log:
                    _trigger_startup_notification()

        mock_start.assert_not_called()
        self.assertTrue(any("Startup notification skipped" in m for m in log.output))

    def test_handler_calls_startup_notification(self):
        from nodes.apps import _trigger_startup_notification

        with patch("nodes.apps._startup_notification") as mock_start:
            with patch("nodes.apps.connections") as mock_connections:
                mock_connections.__getitem__.return_value.ensure_connection.return_value = (
                    None
                )
                _trigger_startup_notification()

        mock_start.assert_called_once()


class NotificationManagerTests(TestCase):
    def test_send_writes_trimmed_lines(self):
        from core.notifications import NotificationManager

        with TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lcd_screen.lck"
            lock.touch()
            manager = NotificationManager(lock_file=lock)
            result = manager.send("a" * 70, "b" * 70)
            self.assertTrue(result)
            content = lock.read_text().splitlines()
            self.assertEqual(content[0], "a" * 64)
            self.assertEqual(content[1], "b" * 64)

    def test_send_falls_back_to_gui(self):
        from core.notifications import NotificationManager

        with TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lcd_screen.lck"
            lock.touch()
            manager = NotificationManager(lock_file=lock)
            manager._gui_display = MagicMock()
            with patch.object(
                manager, "_write_lock_file", side_effect=RuntimeError("boom")
            ):
                result = manager.send("hi", "there")
        self.assertTrue(result)
        manager._gui_display.assert_called_once_with("hi", "there")

    def test_send_uses_gui_when_lock_missing(self):
        from core.notifications import NotificationManager

        with TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lcd_screen.lck"
            manager = NotificationManager(lock_file=lock)
            manager._gui_display = MagicMock()
            result = manager.send("hi", "there")
        self.assertTrue(result)
        manager._gui_display.assert_called_once_with("hi", "there")

    def test_gui_display_uses_windows_toast(self):
        from core.notifications import NotificationManager

        with patch("core.notifications.sys.platform", "win32"):
            mock_notify = MagicMock()
            with patch(
                "core.notifications.plyer_notification",
                MagicMock(notify=mock_notify),
            ):
                manager = NotificationManager()
                manager._gui_display("hi", "there")
        mock_notify.assert_called_once_with(
            title="Arthexis", message="hi\nthere", timeout=6
        )

    def test_gui_display_logs_when_toast_unavailable(self):
        from core.notifications import NotificationManager

        with patch("core.notifications.sys.platform", "win32"):
            with patch("core.notifications.plyer_notification", None):
                with patch("core.notifications.logger") as mock_logger:
                    manager = NotificationManager()
                    manager._gui_display("hi", "there")
        mock_logger.info.assert_called_once_with("%s %s", "hi", "there")


class ContentSampleTransactionTests(TestCase):
    def test_transaction_uuid_behaviour(self):
        sample1 = ContentSample.objects.create(content="a", kind=ContentSample.TEXT)
        self.assertIsNotNone(sample1.transaction_uuid)
        sample2 = ContentSample.objects.create(
            content="b",
            kind=ContentSample.TEXT,
            transaction_uuid=sample1.transaction_uuid,
        )
        self.assertEqual(sample1.transaction_uuid, sample2.transaction_uuid)
        with self.assertRaises(Exception):
            sample1.transaction_uuid = uuid.uuid4()
            sample1.save()


class ContentSampleAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            "clipboard_admin", "admin@example.com", "pass"
        )
        self.client.login(username="clipboard_admin", password="pass")

    @patch("pyperclip.paste")
    def test_add_from_clipboard_creates_sample(self, mock_paste):
        mock_paste.return_value = "clip text"
        url = reverse("admin:nodes_contentsample_from_clipboard")
        response = self.client.get(url, follow=True)
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.TEXT).count(), 1
        )
        sample = ContentSample.objects.filter(kind=ContentSample.TEXT).first()
        self.assertEqual(sample.content, "clip text")
        self.assertEqual(sample.user, self.user)
        self.assertIsNone(sample.node)
        self.assertContains(response, "Text sample added from clipboard")

    @patch("pyperclip.paste")
    def test_add_from_clipboard_sets_node_when_local_exists(self, mock_paste):
        mock_paste.return_value = "clip text"
        Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        url = reverse("admin:nodes_contentsample_from_clipboard")
        self.client.get(url, follow=True)
        sample = ContentSample.objects.filter(kind=ContentSample.TEXT).first()
        self.assertIsNotNone(sample.node)

    @patch("pyperclip.paste")
    def test_add_from_clipboard_skips_duplicate(self, mock_paste):
        mock_paste.return_value = "clip text"
        url = reverse("admin:nodes_contentsample_from_clipboard")
        self.client.get(url, follow=True)
        resp = self.client.get(url, follow=True)
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.TEXT).count(), 1
        )
        self.assertContains(resp, "Duplicate sample not created")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailOutboxTests(TestCase):
    def test_node_send_mail_uses_outbox(self):
        node = Node.objects.create(
            hostname="outboxhost",
            address="127.0.0.1",
            port=8000,
            mac_address="00:11:22:33:aa:bb",
        )
        outbox = EmailOutbox.objects.create(
            node=node, host="smtp.example.com", port=25, username="u", password="p"
        )
        with patch("nodes.models.mailer.send") as ms:
            node.send_mail("sub", "msg", ["to@example.com"])
            ms.assert_called_once_with(
                "sub", "msg", ["to@example.com"], None, outbox=outbox
            )

    def test_node_send_mail_queues_email(self):
        node = Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address="00:11:22:33:cc:dd",
        )
        node.send_mail("sub", "msg", ["to@example.com"])
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.subject, "sub")
        self.assertEqual(email.to, ["to@example.com"])

    def test_string_representation_prefers_from_email(self):
        outbox = EmailOutbox.objects.create(
            host="smtp.example.com",
            port=587,
            username="mailer",
            password="secret",
            from_email="noreply@example.com",
        )

        self.assertEqual(str(outbox), "noreply@example.com")

    def test_string_representation_prefers_username_over_owner(self):
        group = SecurityGroup.objects.create(name="Operators")
        outbox = EmailOutbox.objects.create(
            group=group,
            host="smtp.example.com",
            port=587,
            username="mailer",
            password="secret",
        )

        self.assertEqual(str(outbox), "mailer@smtp.example.com")

    def test_string_representation_does_not_duplicate_email_hostname(self):
        outbox = EmailOutbox.objects.create(
            host="smtp.example.com",
            port=587,
            username="mailer@example.com",
            password="secret",
        )

        self.assertEqual(str(outbox), "mailer@example.com")

    def test_unattached_outbox_used_as_fallback(self):
        EmailOutbox.objects.create(
            group=SecurityGroup.objects.create(name="Attached"),
            host="smtp.attached.example.com",
            port=587,
            username="attached",
            password="secret",
        )
        fallback = EmailOutbox.objects.create(
            host="smtp.fallback.example.com",
            port=587,
            username="fallback",
            password="secret",
        )

        backend = OutboxEmailBackend()
        message = EmailMessage("subject", "body", to=["to@example.com"])

        selected, fallbacks = backend._select_outbox(message)

        self.assertEqual(selected, fallback)
        self.assertEqual(fallbacks, [])

    def test_disabled_outbox_excluded_from_selection(self):
        EmailOutbox.objects.create(
            host="smtp.disabled.example.com",
            port=587,
            username="disabled@example.com",
            password="secret",
            from_email="disabled@example.com",
            is_enabled=False,
        )
        enabled = EmailOutbox.objects.create(
            host="smtp.enabled.example.com",
            port=587,
            username="enabled@example.com",
            password="secret",
        )

        backend = OutboxEmailBackend()
        message = EmailMessage(
            "subject",
            "body",
            from_email="disabled@example.com",
            to=["to@example.com"],
        )

        selected, fallbacks = backend._select_outbox(message)

        self.assertEqual(selected, enabled)
        self.assertEqual(fallbacks, [])


class ClipboardTaskTests(TestCase):
    @patch("nodes.tasks.pyperclip.paste")
    def test_sample_clipboard_task_creates_sample(self, mock_paste):
        mock_paste.return_value = "task text"
        Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        sample_clipboard()
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.TEXT).count(), 1
        )
        sample = ContentSample.objects.filter(kind=ContentSample.TEXT).first()
        self.assertEqual(sample.content, "task text")
        self.assertIsNone(sample.user)
        self.assertIsNotNone(sample.node)
        self.assertEqual(sample.node.hostname, "host")
        # Duplicate should not create another sample
        sample_clipboard()
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.TEXT).count(), 1
        )

    @patch("nodes.tasks.capture_screenshot")
    def test_capture_node_screenshot_task(self, mock_capture):
        node = Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        file_path.write_bytes(b"task")
        mock_capture.return_value = Path("screenshots/test.png")
        capture_node_screenshot("http://example.com")
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 1
        )
        screenshot = ContentSample.objects.filter(kind=ContentSample.IMAGE).first()
        self.assertEqual(screenshot.node, node)
        self.assertEqual(screenshot.path, "screenshots/test.png")
        self.assertEqual(screenshot.method, "TASK")

    @patch("nodes.tasks.capture_screenshot")
    def test_capture_node_screenshot_handles_error(self, mock_capture):
        Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8000,
            mac_address=Node.get_current_mac(),
        )
        mock_capture.side_effect = RuntimeError("boom")
        result = capture_node_screenshot("http://example.com")
        self.assertEqual(result, "")
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 0
        )


class CaptureScreenshotTests(TestCase):
    @patch("nodes.utils.webdriver.Firefox")
    def test_connection_failure_does_not_raise(self, mock_firefox):
        browser = MagicMock()
        mock_firefox.return_value.__enter__.return_value = browser
        browser.get.side_effect = WebDriverException("boom")
        browser.save_screenshot.return_value = True
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        result = capture_screenshot("http://example.com")
        self.assertEqual(result.parent, screenshot_dir)
        browser.save_screenshot.assert_called_once()


class NodeRoleAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            "role_admin", "admin@example.com", "pass"
        )
        self.client.login(username="role_admin", password="pass")

    def test_change_role_nodes(self):
        role = NodeRole.objects.create(name="TestRole")
        node1 = Node.objects.create(
            hostname="n1",
            address="127.0.0.1",
            port=8000,
            mac_address="00:11:22:33:44:55",
            role=role,
        )
        node2 = Node.objects.create(
            hostname="n2",
            address="127.0.0.2",
            port=8000,
            mac_address="00:11:22:33:44:66",
        )
        url = reverse("admin:nodes_noderole_change", args=[role.pk])
        resp = self.client.get(url)
        self.assertContains(resp, f'<option value="{node1.pk}" selected>')
        post_data = {"name": "TestRole", "description": "", "nodes": [node2.pk]}
        resp = self.client.post(url, post_data, follow=True)
        self.assertRedirects(resp, reverse("admin:nodes_noderole_changelist"))
        node1.refresh_from_db()
        node2.refresh_from_db()
        self.assertIsNone(node1.role)
        self.assertEqual(node2.role, role)

    def test_registered_count_displayed(self):
        role = NodeRole.objects.create(name="ViewRole")
        Node.objects.create(
            hostname="n1",
            address="127.0.0.1",
            port=8000,
            mac_address="00:11:22:33:44:77",
            role=role,
        )
        resp = self.client.get(reverse("admin:nodes_noderole_changelist"))
        self.assertContains(resp, '<td class="field-registered">1</td>', html=True)


class NodeFeatureFixtureTests(TestCase):
    def test_rfid_scanner_fixture_includes_control_role(self):
        for name in ("Terminal", "Satellite", "Constellation", "Control"):
            NodeRole.objects.get_or_create(name=name)
        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "node_features__nodefeature_rfid_scanner.json"
        )
        call_command("loaddata", str(fixture_path), verbosity=0)
        feature = NodeFeature.objects.get(slug="rfid-scanner")
        role_names = set(feature.roles.values_list("name", flat=True))
        self.assertIn("Control", role_names)

    def test_ap_router_fixture_limits_roles(self):
        for name in ("Control", "Satellite"):
            NodeRole.objects.get_or_create(name=name)
        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "node_features__nodefeature_ap_router.json"
        )
        call_command("loaddata", str(fixture_path), verbosity=0)
        feature = NodeFeature.objects.get(slug="ap-router")
        role_names = set(feature.roles.values_list("name", flat=True))
        self.assertEqual(role_names, {"Satellite"})


class NodeFeatureTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.node = Node.objects.create(
                hostname="local",
                address="127.0.0.1",
                port=8000,
                mac_address="00:11:22:33:44:55",
                role=self.role,
            )

    def test_lcd_screen_enabled(self):
        feature = NodeFeature.objects.create(slug="lcd-screen", display="LCD")
        feature.roles.add(self.role)
        NodeFeatureAssignment.objects.create(node=self.node, feature=feature)
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.assertTrue(feature.is_enabled)
        NodeFeatureAssignment.objects.filter(node=self.node, feature=feature).delete()
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.assertFalse(feature.is_enabled)

    def test_rfid_scanner_lock(self):
        feature = NodeFeature.objects.create(slug="rfid-scanner", display="RFID")
        feature.roles.add(self.role)
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            locks.mkdir()
            with override_settings(BASE_DIR=base):
                with patch(
                    "nodes.models.Node.get_current_mac",
                    return_value="00:11:22:33:44:55",
                ):
                    self.assertFalse(feature.is_enabled)
                    (locks / "rfid.lck").touch()
                    self.assertTrue(feature.is_enabled)

    def test_gui_toast_detection(self):
        feature = NodeFeature.objects.create(slug="gui-toast", display="GUI Toast")
        feature.roles.add(self.role)
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            with patch("core.notifications.supports_gui_toast", return_value=True):
                self.assertTrue(feature.is_enabled)
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            with patch("core.notifications.supports_gui_toast", return_value=False):
                self.assertFalse(feature.is_enabled)

    def test_role_membership_alone_does_not_enable_feature(self):
        feature = NodeFeature.objects.create(
            slug="custom-feature", display="Custom Feature"
        )
        feature.roles.add(self.role)
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.assertFalse(feature.is_enabled)
        NodeFeatureAssignment.objects.create(node=self.node, feature=feature)
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.assertTrue(feature.is_enabled)

    @patch("nodes.models.Node._has_rpi_camera", return_value=True)
    def test_rpi_camera_detection(self, mock_camera):
        feature = NodeFeature.objects.create(
            slug="rpi-camera", display="Raspberry Pi Camera"
        )
        feature.roles.add(self.role)
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.node.refresh_features()
        self.assertTrue(
            NodeFeatureAssignment.objects.filter(
                node=self.node, feature=feature
            ).exists()
        )

    @patch("nodes.models.Node._has_rpi_camera", side_effect=[True, False])
    def test_rpi_camera_removed_when_unavailable(self, mock_camera):
        feature = NodeFeature.objects.create(
            slug="rpi-camera", display="Raspberry Pi Camera"
        )
        feature.roles.add(self.role)
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.node.refresh_features()
            self.assertTrue(
                NodeFeatureAssignment.objects.filter(
                    node=self.node, feature=feature
                ).exists()
            )
            self.node.refresh_features()
        self.assertFalse(
            NodeFeatureAssignment.objects.filter(
                node=self.node, feature=feature
            ).exists()
        )

    @patch("nodes.models.Node._hosts_gelectriic_ap", return_value=True)
    def test_ap_router_detection(self, mock_hosts):
        control_role, _ = NodeRole.objects.get_or_create(name="Control")
        feature = NodeFeature.objects.create(slug="ap-router", display="AP Router")
        feature.roles.add(control_role)
        mac = "00:11:22:33:44:66"
        with patch("nodes.models.Node.get_current_mac", return_value=mac):
            node = Node.objects.create(
                hostname="control",
                address="127.0.0.1",
                port=8000,
                mac_address=mac,
                role=control_role,
            )
            node.refresh_features()
        self.assertTrue(
            NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()
        )

    @patch("nodes.models.Node._hosts_gelectriic_ap", return_value=True)
    def test_ap_public_wifi_detection(self, mock_hosts):
        control_role, _ = NodeRole.objects.get_or_create(name="Control")
        router = NodeFeature.objects.create(slug="ap-router", display="AP Router")
        router.roles.add(control_role)
        public = NodeFeature.objects.create(
            slug="ap-public-wifi", display="AP Public Wi-Fi"
        )
        public.roles.add(control_role)
        mac = "00:11:22:33:44:88"
        with TemporaryDirectory() as tmp, override_settings(BASE_DIR=Path(tmp)):
            locks = Path(tmp) / "locks"
            locks.mkdir(parents=True, exist_ok=True)
            (locks / "public_wifi_mode.lck").touch()
            with patch("nodes.models.Node.get_current_mac", return_value=mac):
                node = Node.objects.create(
                    hostname="control",
                    address="127.0.0.1",
                    port=8000,
                    mac_address=mac,
                    role=control_role,
                    base_path=str(Path(tmp)),
                )
                node.refresh_features()
        self.assertTrue(
            NodeFeatureAssignment.objects.filter(node=node, feature=public).exists()
        )
        self.assertFalse(
            NodeFeatureAssignment.objects.filter(node=node, feature=router).exists()
        )

    @patch("nodes.models.Node._hosts_gelectriic_ap", side_effect=[True, False])
    def test_ap_router_removed_when_not_hosting(self, mock_hosts):
        control_role, _ = NodeRole.objects.get_or_create(name="Control")
        feature = NodeFeature.objects.create(slug="ap-router", display="AP Router")
        feature.roles.add(control_role)
        mac = "00:11:22:33:44:77"
        with patch("nodes.models.Node.get_current_mac", return_value=mac):
            node = Node.objects.create(
                hostname="control",
                address="127.0.0.1",
                port=8000,
                mac_address=mac,
                role=control_role,
            )
            self.assertTrue(
                NodeFeatureAssignment.objects.filter(
                    node=node, feature=feature
                ).exists()
            )
            node.refresh_features()
        self.assertFalse(
            NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()
        )

    @patch("nodes.models.Node._uses_postgres", return_value=True)
    def test_postgres_detection(self, mock_postgres):
        feature = NodeFeature.objects.create(
            slug="postgres-db", display="PostgreSQL Database"
        )
        feature.roles.add(self.role)
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.node.refresh_features()
        self.assertTrue(
            NodeFeatureAssignment.objects.filter(
                node=self.node, feature=feature
            ).exists()
        )

    @patch("nodes.models.Node._uses_postgres", side_effect=[True, False])
    def test_postgres_removed_when_not_in_use(self, mock_postgres):
        feature = NodeFeature.objects.create(
            slug="postgres-db", display="PostgreSQL Database"
        )
        feature.roles.add(self.role)
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.node.refresh_features()
        self.assertTrue(
            NodeFeatureAssignment.objects.filter(
                node=self.node, feature=feature
            ).exists()
        )
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.node.refresh_features()
        self.assertFalse(
            NodeFeatureAssignment.objects.filter(
                node=self.node, feature=feature
            ).exists()
        )


class NodeRpiCameraDetectionTests(TestCase):
    @patch("nodes.models.subprocess.run")
    @patch("nodes.models.shutil.which")
    @patch("nodes.models.os.access")
    @patch("nodes.models.os.stat")
    @patch("nodes.models.Path.exists")
    def test_has_rpi_camera_true(
        self,
        mock_exists,
        mock_stat,
        mock_access,
        mock_which,
        mock_run,
    ):
        mock_exists.return_value = True
        mock_stat.return_value = SimpleNamespace(st_mode=stat.S_IFCHR)
        mock_access.return_value = True
        mock_which.side_effect = lambda name: f"/usr/bin/{name}"
        mock_run.return_value = SimpleNamespace(returncode=0)

        self.assertTrue(Node._has_rpi_camera())
        self.assertEqual(mock_which.call_count, len(Node.RPI_CAMERA_BINARIES))
        self.assertEqual(mock_run.call_count, len(Node.RPI_CAMERA_BINARIES))

    @patch("nodes.models.subprocess.run")
    @patch("nodes.models.shutil.which")
    @patch("nodes.models.os.access")
    @patch("nodes.models.os.stat")
    @patch("nodes.models.Path.exists")
    def test_has_rpi_camera_missing_device(
        self,
        mock_exists,
        mock_stat,
        mock_access,
        mock_which,
        mock_run,
    ):
        mock_exists.return_value = False

        self.assertFalse(Node._has_rpi_camera())
        mock_stat.assert_not_called()
        mock_access.assert_not_called()
        mock_which.assert_not_called()
        mock_run.assert_not_called()

    @patch("nodes.models.subprocess.run")
    @patch("nodes.models.shutil.which")
    @patch("nodes.models.os.access")
    @patch("nodes.models.os.stat")
    @patch("nodes.models.Path.exists")
    def test_has_rpi_camera_missing_tool(
        self,
        mock_exists,
        mock_stat,
        mock_access,
        mock_which,
        mock_run,
    ):
        mock_exists.return_value = True
        mock_stat.return_value = SimpleNamespace(st_mode=stat.S_IFCHR)
        mock_access.return_value = True
        mock_run.return_value = SimpleNamespace(returncode=0)

        def tool_lookup(name):
            if name == Node.RPI_CAMERA_BINARIES[-1]:
                return None
            return f"/usr/bin/{name}"

        mock_which.side_effect = tool_lookup

        self.assertFalse(Node._has_rpi_camera())
        missing_index = Node.RPI_CAMERA_BINARIES.index(Node.RPI_CAMERA_BINARIES[-1])
        self.assertEqual(mock_run.call_count, missing_index)
