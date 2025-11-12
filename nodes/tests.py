import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
import pytest

try:  # Use the pytest-specific setup when available for database readiness
    from tests.conftest import safe_setup as _safe_setup  # type: ignore
except Exception:  # pragma: no cover - fallback for direct execution
    _safe_setup = None

if _safe_setup is not None:
    _safe_setup()
else:  # pragma: no cover - fallback when pytest fixtures are unavailable
    django.setup()

from pathlib import Path
from types import SimpleNamespace
import unittest.mock as mock
from unittest.mock import patch, call, MagicMock
from django.core import mail
from django.core.cache import cache
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
from datetime import datetime, timedelta

from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib import admin
from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.admin import helpers
from django.contrib.auth.models import Permission
from django_celery_beat.models import IntervalSchedule, PeriodicTask
from core.celery_utils import (
    periodic_task_name_variants,
    slugify_task_name,
)
from django.conf import settings
from django.utils import timezone
from urllib.parse import quote, urlparse
from dns import resolver as dns_resolver
from . import dns as dns_utils
from selenium.common.exceptions import WebDriverException
from nodes.tasks import update_all_nodes_information
from .classifiers import run_default_classifiers
from .utils import capture_rpi_snapshot, capture_screenshot, save_screenshot
from .feature_checks import feature_checks
from nodes import reports
from django.db.utils import DatabaseError
from .admin import NodeAdmin
from .models import (
    Node,
    ContentClassifier,
    ContentClassification,
    ContentSample,
    ContentTag,
    NodeRole,
    NodeFeature,
    NodeFeatureAssignment,
    NetMessage,
    PendingNetMessage,
    NodeManager,
    DNSRecord,
)
from .backends import OutboxEmailBackend
from .tasks import (
    capture_node_screenshot,
    kickstart_constellation_udp,
    poll_unreachable_upstream,
    sample_clipboard,
)
from . import tasks as node_tasks
from ocpp.models import Charger
from protocols.models import CPForwarder
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from core.models import Package, PackageRelease, SecurityGroup, RFID, CustomerAccount, Todo
from requests.exceptions import SSLError
from teams.models import EmailOutbox


class NodeBadgeColorTests(TestCase):
    def setUp(self):
        self.watchtower, _ = NodeRole.objects.get_or_create(name="Watchtower")
        self.control, _ = NodeRole.objects.get_or_create(name="Control")

    def test_watchtower_role_defaults_to_goldenrod(self):
        node = Node.objects.create(
            hostname="watchtower",
            address="10.1.0.1",
            port=8888,
            mac_address="00:aa:bb:cc:dd:01",
            role=self.watchtower,
        )
        self.assertEqual(node.badge_color, "#daa520")

    def test_control_role_defaults_to_deep_purple(self):
        node = Node.objects.create(
            hostname="control",
            address="10.1.0.2",
            port=8001,
            mac_address="00:aa:bb:cc:dd:02",
            role=self.control,
        )
        self.assertEqual(node.badge_color, "#673ab7")

    def test_custom_badge_color_is_preserved(self):
        node = Node.objects.create(
            hostname="custom",
            address="10.1.0.3",
            port=8002,
            mac_address="00:aa:bb:cc:dd:03",
            role=self.watchtower,
            badge_color="#123456",
        )
        self.assertEqual(node.badge_color, "#123456")


class NodeTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="nodeuser", password="pwd")
        self.client.force_login(self.user)
        NodeRole.objects.get_or_create(name="Terminal")
        NodeRole.objects.get_or_create(name="Interface")

    def test_terminal_role_enables_clipboard_feature_by_default(self):
        role = NodeRole.objects.get(name="Terminal")
        feature, _ = NodeFeature.objects.get_or_create(
            slug="clipboard-poll", defaults={"display": "Clipboard Poll"}
        )
        feature.roles.add(role)

        node = Node.objects.create(
            hostname="terminal-node",
            address="10.0.0.5",
            port=8888,
            mac_address="aa:bb:cc:dd:ee:ff",
            role=role,
        )

        self.assertTrue(node.has_feature("clipboard-poll"))


class NodeGetLocalDatabaseUnavailableTests(SimpleTestCase):
    def test_get_local_handles_database_errors(self):
        with patch.object(Node.objects, "filter", side_effect=DatabaseError("fail")):
            with self.assertLogs("nodes.models", level="DEBUG") as logs:
                result = Node.get_local()

        self.assertIsNone(result)
        self.assertTrue(
            any("Node.get_local skipped: database unavailable" in message for message in logs.output)
        )


class NodeGetLocalTests(TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(username="localtester", password="pwd")
        self.client.force_login(self.user)

    def test_normalize_relation_handles_various_inputs(self):
        self.assertEqual(
            Node.normalize_relation(Node.Relation.UPSTREAM),
            Node.Relation.UPSTREAM,
        )
        self.assertEqual(
            Node.normalize_relation(None),
            Node.Relation.PEER,
        )
        self.assertEqual(
            Node.normalize_relation("Upstream"),
            Node.Relation.UPSTREAM,
        )
        self.assertEqual(
            Node.normalize_relation("DOWNSTREAM"),
            Node.Relation.DOWNSTREAM,
        )
        self.assertEqual(
            Node.normalize_relation("peer"),
            Node.Relation.PEER,
        )
        self.assertEqual(
            Node.normalize_relation("unexpected"),
            Node.Relation.PEER,
        )

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
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                ):
                    node, created = Node.register_current()
        self.assertEqual(PackageRelease.objects.count(), 0)
        self.assertIsNotNone(node)
        self.assertTrue(created)
        self.assertEqual(node.current_relation, Node.Relation.SELF)

    def test_register_current_updates_role_from_lock_file(self):
        NodeRole.objects.get_or_create(name="Terminal")
        NodeRole.objects.get_or_create(name="Watchtower")
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            lock_dir = base / "locks"
            lock_dir.mkdir(parents=True, exist_ok=True)
            role_file = lock_dir / "role.lck"
            role_file.write_text("Terminal")
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:aa:bb:cc:dd:ee",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="role-host"),
                    patch(
                        "nodes.models.socket.gethostbyname", return_value="127.0.0.1"
                    ),
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                    patch.object(Node, "notify_peers_of_update"),
                ):
                    node, created = Node.register_current()
            self.assertTrue(created)
            self.assertEqual(node.role.name, "Terminal")

            role_file.write_text("Watchtower")
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:aa:bb:cc:dd:ee",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="role-host"),
                    patch(
                        "nodes.models.socket.gethostbyname", return_value="127.0.0.1"
                    ),
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                    patch.object(Node, "notify_peers_of_update"),
                ):
                    _, created_again = Node.register_current()

            self.assertFalse(created_again)
            node.refresh_from_db()
            self.assertEqual(node.role.name, "Watchtower")

            role_file.write_text("Constellation")
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:aa:bb:cc:dd:ee",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="role-host"),
                    patch(
                        "nodes.models.socket.gethostbyname", return_value="127.0.0.1"
                    ),
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                    patch.object(Node, "notify_peers_of_update"),
                ):
                    Node.register_current()

            node.refresh_from_db()
            self.assertEqual(node.role.name, "Watchtower")

    def test_register_current_respects_node_hostname_env(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            with override_settings(BASE_DIR=base):
                with (
                    patch.dict(os.environ, {"NODE_HOSTNAME": "gway-002"}, clear=False),
                    patch("nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"),
                    patch("nodes.models.socket.gethostname", return_value="localhost"),
                    patch("nodes.models.socket.gethostbyname", return_value="127.0.0.1"),
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                    patch.object(Node, "notify_peers_of_update"),
                ):
                    node, created = Node.register_current()
        self.assertTrue(created)
        self.assertEqual(node.hostname, "gway-002")
        self.assertEqual(node.public_endpoint, "gway-002")

    def test_register_current_respects_public_endpoint_env(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            with override_settings(BASE_DIR=base):
                with (
                    patch.dict(
                        os.environ,
                        {"NODE_HOSTNAME": "gway-alpha", "NODE_PUBLIC_ENDPOINT": "gway-002"},
                        clear=False,
                    ),
                    patch("nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:56"),
                    patch("nodes.models.socket.gethostname", return_value="localhost"),
                    patch("nodes.models.socket.gethostbyname", return_value="127.0.0.1"),
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                    patch.object(Node, "notify_peers_of_update"),
                ):
                    node, created = Node.register_current()
        self.assertTrue(created)
        self.assertEqual(node.hostname, "gway-alpha")
        self.assertEqual(node.public_endpoint, "gway-002")

    def test_register_and_list_node(self):
        response = self.client.post(
            reverse("register-node"),
            data={
                "hostname": "local",
                "address": "127.0.0.1",
                "port": 8888,
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

    def test_register_node_generates_unique_public_endpoint(self):
        url = reverse("register-node")
        User = get_user_model()
        user = User.objects.create_user(username="registrar", password="pwd")
        self.client.force_login(user)
        first = self.client.post(
            url,
            data={
                "hostname": "duplicate-host",
                "address": "10.0.0.10",
                "port": 8080,
                "mac_address": "00:11:22:33:aa:bb",
            },
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)
        node_one = Node.objects.get(mac_address="00:11:22:33:aa:bb")
        self.assertEqual(node_one.public_endpoint, "duplicate-host")

        second = self.client.post(
            url,
            data={
                "hostname": "duplicate-host",
                "address": "10.0.0.11",
                "port": 8081,
                "mac_address": "00:11:22:33:aa:cc",
            },
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 200)
        node_two = Node.objects.get(mac_address="00:11:22:33:aa:cc")

        self.assertNotEqual(node_one.public_endpoint, node_two.public_endpoint)
        self.assertTrue(node_two.public_endpoint.startswith("duplicate-host-"))

    def test_register_node_accepts_network_hostname_without_address(self):
        response = self.client.post(
            reverse("register-node"),
            data={
                "hostname": "domain-node",
                "network_hostname": "domain-node.example.com",
                "port": 8050,
                "mac_address": "aa:bb:cc:dd:ee:ff",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        node = Node.objects.get(mac_address="aa:bb:cc:dd:ee:ff")
        self.assertEqual(node.network_hostname, "domain-node.example.com")
        self.assertIsNone(node.address)
        self.assertIsNone(node.ipv4_address)
        self.assertIsNone(node.ipv6_address)

    def test_register_node_populates_missing_ip_fields_from_address(self):
        response = self.client.post(
            reverse("register-node"),
            data={
                "hostname": "address-node",
                "address": "203.0.113.10",
                "port": 8040,
                "mac_address": "aa:bb:cc:dd:ee:01",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        node = Node.objects.get(mac_address="aa:bb:cc:dd:ee:01")
        self.assertEqual(node.address, "203.0.113.10")
        self.assertEqual(node.ipv4_address, "203.0.113.10")
        self.assertIsNone(node.ipv6_address)

    def test_register_node_stores_multiple_ipv4_addresses(self):
        payload = {
            "hostname": "multi-ip",
            "address": "198.51.100.10",
            "port": 8041,
            "mac_address": "aa:bb:cc:dd:ee:99",
            "ipv4_address": ["198.51.100.10", "127.0.0.1", "203.0.113.20"],
        }
        response = self.client.post(
            reverse("register-node"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        node = Node.objects.get(mac_address="aa:bb:cc:dd:ee:99")
        self.assertEqual(node.ipv4_address, "198.51.100.10,203.0.113.20")
        self.assertEqual(
            node.get_ipv4_addresses(), ["198.51.100.10", "203.0.113.20"]
        )
        self.assertNotIn("127.0.0.1", node.ipv4_address)

    def test_get_best_ip_ignores_non_ip_values(self):
        node = Node.objects.create(
            hostname="best-ip",
            address="gateway.local",
            ipv4_address="198.51.100.5",
            port=8888,
            mac_address="00:11:22:33:44:77",
        )
        self.assertEqual(node.get_best_ip(), "198.51.100.5")

    def test_get_best_ip_prefers_constellation_address(self):
        node = Node.objects.create(
            hostname="best-constellation",
            address="192.0.2.8",
            ipv4_address="192.0.2.8",
            constellation_ip="10.88.0.24",
            port=8888,
            mac_address="00:11:22:33:44:88",
        )
        self.assertEqual(node.get_best_ip(), "10.88.0.24")

    def test_get_remote_host_candidates_prioritize_constellation_ip(self):
        node = Node(
            hostname="remote-candidate",
            address="192.0.2.9",
            ipv4_address="192.0.2.9",
            constellation_ip="10.88.0.25",
            port=8888,
        )
        hosts = node.get_remote_host_candidates()
        self.assertEqual(hosts[0], "10.88.0.25")
        self.assertIn("192.0.2.9", hosts)

    def test_get_remote_host_candidates_preserve_ipv4_order(self):
        node = Node(
            hostname="remote-order",
            ipv4_address="198.51.100.10,203.0.113.20",
            port=8888,
        )
        hosts = node.get_remote_host_candidates()
        ipv4_hosts = Node.sanitize_ipv4_addresses(hosts)
        self.assertEqual(ipv4_hosts, ["198.51.100.10", "203.0.113.20"])

    def test_register_node_requires_contact_information(self):
        response = self.client.post(
            reverse("register-node"),
            data={
                "hostname": "missing-host",
                "port": 8051,
                "mac_address": "aa:bb:cc:dd:ee:00",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertIn("at least one", detail)
        self.assertIn("constellation_ip", detail)

    def test_register_node_accepts_constellation_ip(self):
        response = self.client.post(
            reverse("register-node"),
            data={
                "hostname": "overlay-node",
                "constellation_ip": "10.88.0.42",
                "port": 8052,
                "mac_address": "aa:bb:cc:dd:ee:07",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        node = Node.objects.get(mac_address="aa:bb:cc:dd:ee:07")
        self.assertEqual(node.constellation_ip, "10.88.0.42")

    def test_register_node_assigns_interface_role_and_returns_uuid(self):
        NodeRole.objects.get_or_create(name="Interface")
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        token = "interface-token"
        signature = base64.b64encode(
            private_key.sign(
                token.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        ).decode()
        mac = "aa:bb:cc:dd:ee:99"
        payload = {
            "hostname": "interface",
            "address": "127.0.0.1",
            "port": 8443,
            "mac_address": mac,
            "public_key": public_bytes,
            "token": token,
            "signature": signature,
            "role": "Interface",
        }
        response = self.client.post(
            reverse("register-node"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("uuid", data)
        node = Node.objects.get(mac_address=mac)
        self.assertEqual(node.role.name, "Interface")

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
                "port": 8888,
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
                "port": 8888,
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

    def test_register_node_marks_nonrelease_version(self):
        package = Package.objects.create(name="pkg-node", is_active=False)
        PackageRelease.objects.create(
            package=package,
            version="2.0.0",
            revision="0" * 40,
        )

        node = Node.objects.create(
            hostname="friend",
            address="10.1.1.5",
            port=8123,
            mac_address="aa:bb:cc:dd:ee:11",
            installed_version="1.0.0",
            installed_revision="rev-old",
        )
        user = get_user_model().objects.create_user(
            username="node-registrar", password="pwd"
        )
        self.client.force_login(user)
        url = reverse("register-node")
        payload = {
            "hostname": "friend",
            "address": "10.1.1.5",
            "port": 8123,
            "mac_address": "aa:bb:cc:dd:ee:11",
            "installed_version": "2.0.0",
            "installed_revision": "1" * 40,
        }
        with patch("nodes.models.notify_async") as mock_notify:
            response = self.client.post(
                url, data=json.dumps(payload), content_type="application/json"
            )
        self.assertEqual(response.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(node.installed_version, "2.0.0")
        self.assertEqual(node.installed_revision, "1" * 40)
        mock_notify.assert_called_once()
        subject, body = mock_notify.call_args[0]
        self.assertEqual(subject, "UP friend")
        self.assertEqual(body, "v2.0.0+ r111111")

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
            "port": 8888,
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
            "port": 8888,
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
            "port": 8888,
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


class NodeEnsureKeysTests(TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.override = override_settings(BASE_DIR=self.base)
        self.override.enable()
        self.node = Node.objects.create(
            hostname="ensure-host",
            address="127.0.0.1",
            port=8888,
            mac_address="00:11:22:33:44:55",
        )

    def tearDown(self):
        self.override.disable()
        self.tempdir.cleanup()

    def test_regenerates_missing_keys(self):
        self.node.ensure_keys()
        security_dir = self.base / "security"
        priv_path = security_dir / self.node.public_endpoint
        pub_path = security_dir / f"{self.node.public_endpoint}.pub"
        original_public = self.node.public_key
        priv_path.unlink()
        pub_path.unlink()

        self.node.ensure_keys()

        self.assertTrue(priv_path.exists())
        self.assertTrue(pub_path.exists())
        self.assertNotEqual(self.node.public_key, original_public)

    def test_regenerates_outdated_keys(self):
        self.node.ensure_keys()
        security_dir = self.base / "security"
        priv_path = security_dir / self.node.public_endpoint
        pub_path = security_dir / f"{self.node.public_endpoint}.pub"
        original_private = priv_path.read_bytes()
        original_public = pub_path.read_bytes()

        old_time = (timezone.now() - timedelta(seconds=5)).timestamp()
        os.utime(priv_path, (old_time, old_time))
        os.utime(pub_path, (old_time, old_time))

        with override_settings(NODE_KEY_MAX_AGE=timedelta(seconds=1)):
            self.node.ensure_keys()

        self.node.refresh_from_db()
        self.assertNotEqual(priv_path.read_bytes(), original_private)
        self.assertNotEqual(pub_path.read_bytes(), original_public)
        self.assertNotEqual(self.node.public_key, original_public.decode())


class NodeInfoViewTests(TestCase):
    def setUp(self):
        self.mac = "02:00:00:00:00:01"
        self.patcher = patch("nodes.models.Node.get_current_mac", return_value=self.mac)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.node = Node.objects.create(
            hostname="local",
            network_hostname="local.example.com",
            address="10.0.0.10",
            ipv4_address="10.0.0.10",
            ipv6_address="2001:db8::10",
            constellation_ip="10.88.0.60",
            port=8888,
            mac_address=self.mac,
            public_endpoint="local",
            current_relation=Node.Relation.SELF,
        )
        self.url = reverse("node-info")

    def test_returns_https_port_for_secure_domain_request(self):
        with self.settings(ALLOWED_HOSTS=["arthexis.com"]):
            response = self.client.get(
                self.url,
                secure=True,
                HTTP_HOST="arthexis.com",
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["port"], 443)

    def test_returns_http_port_for_plain_domain_request(self):
        with self.settings(ALLOWED_HOSTS=["arthexis.com"]):
            response = self.client.get(
                self.url,
                HTTP_HOST="arthexis.com",
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["port"], 80)
        self.assertEqual(payload.get("network_hostname"), "local.example.com")
        self.assertIn("local.example.com", payload.get("contact_hosts", []))

    def test_preserves_explicit_port_in_host_header(self):
        with self.settings(ALLOWED_HOSTS=["arthexis.com"]):
            response = self.client.get(
                self.url,
                secure=True,
                HTTP_HOST="arthexis.com:8443",
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["port"], 8443)

    def test_includes_role_in_payload(self):
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.node.role = role
        self.node.save(update_fields=["role"])

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("role"), "Terminal")
        self.assertEqual(payload.get("ipv4_address"), "10.0.0.10")
        self.assertEqual(payload.get("ipv6_address"), "2001:db8::10")

    def test_includes_constellation_ip(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("constellation_ip"), "10.88.0.60")
        self.assertIn("10.88.0.60", payload.get("contact_hosts", []))

    def test_includes_version_information(self):
        self.node.installed_version = "4.5.6"
        self.node.installed_revision = "abcdef123456"
        self.node.save(update_fields=["installed_version", "installed_revision"])

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("installed_version"), "4.5.6")
        self.assertEqual(payload.get("installed_revision"), "abcdef123456")


class ConstellationSetupViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.watchtower_role, _ = NodeRole.objects.get_or_create(name="Watchtower")
        self.local_mac = "02:00:00:00:00:20"
        self.local_node = Node.objects.create(
            hostname="watchtower",
            network_hostname="watchtower.local",
            address="203.0.113.1",
            port=8888,
            mac_address=self.local_mac,
            current_relation=Node.Relation.SELF,
            role=self.watchtower_role,
        )
        self.url = reverse("node-constellation-setup")
        self.state_dir = Path(settings.BASE_DIR) / "security" / "wireguard"
        shutil.rmtree(self.state_dir, ignore_errors=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.public_key_path = self.state_dir / "wg-constellation.pub"
        self.public_key_path.write_text("hub-public-key")
        locks_dir = Path(settings.BASE_DIR) / "locks"
        locks_dir.mkdir(exist_ok=True)
        self.lock_path = locks_dir / "constellation_ip.lck"
        if self.lock_path.exists():
            self.lock_path.unlink()
        self.mac_patch = patch(
            "nodes.models.Node.get_current_mac", return_value=self.local_mac
        )
        self.mac_patch.start()
        self.addCleanup(self._cleanup_constellation_state)

    def _cleanup_constellation_state(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass
        self.mac_patch.stop()

    def test_assigns_constellation_ip_to_peer(self):
        peer = Node.objects.create(
            hostname="peer-node",
            address="198.51.100.10",
            port=8080,
            mac_address="02:00:00:00:00:21",
        )
        payload = {
            "mac_address": peer.mac_address,
            "public_key": "peer-public-key",
            "hostname": peer.hostname,
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        peer.refresh_from_db()
        self.assertEqual(peer.constellation_ip, data["assigned_ip"])
        self.assertTrue(peer.constellation_device)
        self.assertTrue(peer.constellation_device.startswith("gw"))
        self.assertEqual(data["constellation_device"], peer.constellation_device)
        self.local_node.refresh_from_db()
        self.assertEqual(data["hub"]["constellation_ip"], self.local_node.constellation_ip)
        self.assertEqual(
            data["hub"]["constellation_device"], self.local_node.constellation_device
        )
        self.assertTrue((self.state_dir / "peers.json").exists())

    def test_reuses_existing_constellation_ip(self):
        peer = Node.objects.create(
            hostname="existing-peer",
            address="198.51.100.11",
            port=8081,
            mac_address="02:00:00:00:00:22",
            constellation_ip="10.88.0.50",
        )
        payload = {
            "mac_address": peer.mac_address,
            "public_key": "peer-existing-key",
            "hostname": peer.hostname,
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        peer.refresh_from_db()
        self.assertEqual(peer.constellation_ip, "10.88.0.50")
        self.assertEqual(data["assigned_ip"], "10.88.0.50")
        self.assertTrue(peer.constellation_device)

    def test_requires_watchtower_role(self):
        terminal_role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.local_node.role = terminal_role
        self.local_node.save(update_fields=["role"])
        peer = Node.objects.create(
            hostname="non-watch", address="198.51.100.99", port=8083, mac_address="02:00:00:00:00:23"
        )
        payload = {
            "mac_address": peer.mac_address,
            "public_key": "peer",  # minimal payload
            "hostname": peer.hostname,
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)

    def test_watchtower_request_includes_peers(self):
        peer = Node.objects.create(
            hostname="peer-two",
            address="198.51.100.12",
            port=8082,
            mac_address="02:00:00:00:00:24",
        )
        self.client.post(
            self.url,
            data=json.dumps(
                {
                    "mac_address": peer.mac_address,
                    "public_key": "peer-two-key",
                    "hostname": peer.hostname,
                }
            ),
            content_type="application/json",
        )
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "mac_address": self.local_mac,
                    "public_key": "watchtower-key",
                    "hostname": self.local_node.hostname,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["assigned_ip"], data["hub"]["constellation_ip"])
        self.assertGreaterEqual(len(data.get("peers", [])), 1)
        for peer_entry in data.get("peers", []):
            self.assertTrue(peer_entry.get("constellation_device"))
            self.assertTrue(peer_entry["constellation_device"].startswith("gw"))

    def test_assigns_incremental_constellation_device_names(self):
        first_peer = Node.objects.create(
            hostname="peer-one",
            address="198.51.100.20",
            port=8084,
            mac_address="02:00:00:00:00:31",
        )
        second_peer = Node.objects.create(
            hostname="peer-two",
            address="198.51.100.21",
            port=8085,
            mac_address="02:00:00:00:00:32",
        )

        for peer in (first_peer, second_peer):
            payload = {
                "mac_address": peer.mac_address,
                "public_key": f"{peer.hostname}-key",
                "hostname": peer.hostname,
            }
            response = self.client.post(
                self.url,
                data=json.dumps(payload),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        self.local_node.refresh_from_db()
        first_peer.refresh_from_db()
        second_peer.refresh_from_db()

        self.assertEqual(self.local_node.constellation_device, "gw1")
        self.assertEqual(first_peer.constellation_device, "gw2")
        self.assertEqual(second_peer.constellation_device, "gw3")

class RegisterVisitorNodeMessageTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="visitor", password="pwd")
        self.client.force_login(self.user)
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.visitor = Node.objects.create(
            hostname="visitor-node",
            address="10.0.0.100",
            port=8888,
            mac_address="00:10:20:30:40:50",
            role=self.role,
        )

    def test_register_node_does_not_emit_join_message_when_upstream_added(self):
        payload = {
            "hostname": "host-node",
            "address": "10.0.0.10",
            "port": 8100,
            "mac_address": "aa:bb:cc:dd:ee:01",
            "current_relation": "Upstream",
        }
        with patch("nodes.views.Node.get_local", return_value=self.visitor), patch.object(
            NetMessage, "broadcast"
        ) as mock_broadcast:
            response = self.client.post(
                reverse("register-node"),
                data=json.dumps(payload),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        mock_broadcast.assert_not_called()

    def test_register_node_skips_message_when_not_upstream(self):
        payload = {
            "hostname": "remote-node",
            "address": "10.0.0.11",
            "port": 8101,
            "mac_address": "aa:bb:cc:dd:ee:02",
            "current_relation": "Downstream",
        }
        with patch("nodes.views.Node.get_local", return_value=self.visitor), patch.object(
            NetMessage, "broadcast"
        ) as mock_broadcast:
            response = self.client.post(
                reverse("register-node"),
                data=json.dumps(payload),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        mock_broadcast.assert_not_called()


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
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                    patch.object(Node, "notify_peers_of_update") as mock_notify,
                ):
                    Node.register_current()
        mock_notify.assert_called_once()

    def test_register_current_can_skip_peer_notifications(self):
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
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
                    patch("nodes.models.revision.get_revision", return_value="rev"),
                    patch.object(Node, "ensure_keys"),
                    patch.object(Node, "notify_peers_of_update") as mock_notify,
                ):
                    Node.register_current(notify_peers=False)
        mock_notify.assert_not_called()

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
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
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
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
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
                    patch.object(Node, "_resolve_ip_addresses", return_value=([], [])),
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
                    patch("nodes.models.socket.getfqdn", return_value="localnode.example.com"),
                    patch(
                        "nodes.models.socket.gethostbyname",
                        return_value="192.168.1.5",
                    ),
                    patch.dict(os.environ, {"HOSTNAME": ""}, clear=False),
                    patch.object(
                        Node,
                        "_resolve_ip_addresses",
                        return_value=(
                            ["192.168.1.5", "93.184.216.34"],
                            ["fe80::1", "2001:4860:4860::8888"],
                        ),
                    ),
                    patch("nodes.models.revision.get_revision", return_value="newrev"),
                    patch("requests.post") as mock_post,
                ):
                    Node.objects.create(
                        hostname="localnode",
                        address="192.168.1.5",
                        port=8888,
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
        self.assertEqual(payload.get("network_hostname"), "localnode.example.com")
        self.assertEqual(
            payload.get("ipv4_address"), "93.184.216.34,192.168.1.5"
        )
        self.assertEqual(payload.get("ipv6_address"), "2001:4860:4860::8888")

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
                    patch("nodes.models.socket.getfqdn", return_value="samever.example.com"),
                    patch(
                        "nodes.models.socket.gethostbyname",
                        return_value="192.168.1.6",
                    ),
                    patch.dict(os.environ, {"HOSTNAME": ""}, clear=False),
                    patch.object(
                        Node,
                        "_resolve_ip_addresses",
                        return_value=(
                            ["192.168.1.6", "93.184.216.35"],
                            ["fe80::2", "2001:4860:4860::8844"],
                        ),
                    ),
                    patch("nodes.models.revision.get_revision", return_value="rev1"),
                    patch("requests.post") as mock_post,
                ):
                    Node.objects.create(
                        hostname="samever",
                        address="192.168.1.6",
                        port=8888,
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
        self.assertEqual(payload.get("network_hostname"), "samever.example.com")
        self.assertEqual(
            payload.get("ipv4_address"), "93.184.216.35,192.168.1.6"
        )
        self.assertEqual(payload.get("ipv6_address"), "2001:4860:4860::8844")

    def test_register_current_populates_network_fields(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            with override_settings(BASE_DIR=base):
                with (
                    patch(
                        "nodes.models.Node.get_current_mac",
                        return_value="00:12:34:56:78:90",
                    ),
                    patch("nodes.models.socket.gethostname", return_value="nodehost"),
                    patch("nodes.models.socket.getfqdn", return_value="nodehost.example.com"),
                    patch(
                        "nodes.models.socket.gethostbyname",
                        return_value="10.0.0.5",
                    ),
                    patch.dict(os.environ, {"HOSTNAME": ""}, clear=False),
                    patch.object(
                        Node,
                        "_resolve_ip_addresses",
                        return_value=(
                            ["10.0.0.5", "93.184.216.36"],
                            ["fe80::5", "2001:4860:4860::1"],
                        ),
                    ),
                    patch("nodes.models.revision.get_revision", return_value="revX"),
                    patch.object(Node, "ensure_keys"),
                    patch.object(Node, "notify_peers_of_update"),
                ):
                    node, created = Node.register_current()
        self.assertTrue(created)
        self.assertEqual(node.network_hostname, "nodehost.example.com")
        self.assertEqual(node.ipv4_address, "93.184.216.36,10.0.0.5")
        self.assertEqual(node.ipv6_address, "2001:4860:4860::1")
        self.assertEqual(node.address, "93.184.216.36")

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
        node.installed_version = "1.2.3"
        node.installed_revision = "rev123456"
        node.save(update_fields=["installed_version", "installed_revision"])
        url = reverse("node-public-endpoint", args=[node.public_endpoint])

        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 200)
        payload = get_resp.json()
        self.assertEqual(payload["hostname"], "public")
        self.assertEqual(payload.get("installed_version"), "1.2.3")
        self.assertEqual(payload.get("installed_revision"), "rev123456")

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
            port=8888,
            mac_address="00:11:22:33:44:cc",
            public_key=public_key,
        )
        target_role, _ = NodeRole.objects.get_or_create(name="Control")
        feature, _ = NodeFeature.objects.get_or_create(
            slug="net-message", defaults={"display": "Net Message"}
        )
        target = Node.objects.create(
            hostname="target",
            address="10.0.0.2",
            port=8001,
            mac_address="00:11:22:33:44:dd",
            role=target_role,
        )
        msg_id = str(uuid.uuid4())
        payload = {
            "uuid": msg_id,
            "subject": "hello",
            "body": "world",
            "seen": [],
            "sender": str(sender.uuid),
            "origin": str(sender.uuid),
            "filter_node": str(target.uuid),
            "filter_node_feature": feature.slug,
            "filter_node_role": target_role.name,
            "filter_current_relation": Node.Relation.UPSTREAM,
            "filter_installed_version": "1.0.0",
            "filter_installed_revision": "rev123",
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
        self.assertEqual(message.filter_node, target)
        self.assertEqual(message.filter_node_feature, feature)
        self.assertEqual(message.filter_node_role, target_role)
        self.assertEqual(message.filter_current_relation, Node.Relation.UPSTREAM)
        self.assertEqual(message.filter_installed_version, "1.0.0")
        self.assertEqual(message.filter_installed_revision, "rev123")

    def test_net_message_updates_existing_record(self):
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
            hostname="sender-update",
            address="10.0.0.50",
            port=8010,
            mac_address="00:11:22:33:44:fe",
            public_key=public_key,
        )
        existing_reach, _ = NodeRole.objects.get_or_create(name="Terminal")
        new_reach, _ = NodeRole.objects.get_or_create(name="Control")
        old_filter_node = Node.objects.create(
            hostname="legacy-filter",
            address="10.0.0.60",
            port=8020,
            mac_address="00:11:22:33:44:fd",
        )
        new_filter_node = Node.objects.create(
            hostname="new-filter",
            address="10.0.0.61",
            port=8021,
            mac_address="00:11:22:33:44:fc",
        )
        old_feature = NodeFeature.objects.create(
            slug=f"legacy-feature-{uuid.uuid4().hex}", display="Legacy Feature"
        )
        new_feature = NodeFeature.objects.create(
            slug=f"new-feature-{uuid.uuid4().hex}", display="New Feature"
        )
        old_filter_role = NodeRole.objects.create(
            name=f"LegacyRole-{uuid.uuid4().hex}"
        )
        new_filter_role = NodeRole.objects.create(name=f"NewRole-{uuid.uuid4().hex}")
        origin_node = Node.objects.create(
            hostname="origin-node",
            address="10.0.0.70",
            port=8030,
            mac_address="00:11:22:33:44:fb",
        )
        msg_uuid = uuid.uuid4()
        original = NetMessage.objects.create(
            uuid=msg_uuid,
            subject="old subject",
            body="old body",
            reach=existing_reach,
            filter_node=old_filter_node,
            filter_node_feature=old_feature,
            filter_node_role=old_filter_role,
            filter_current_relation=Node.Relation.PEER,
            filter_installed_version="0.9.0",
            filter_installed_revision="oldrev",
        )
        payload = {
            "uuid": str(msg_uuid),
            "subject": "updated subject",
            "body": "updated body",
            "seen": [str(uuid.uuid4()), str(uuid.uuid4())],
            "sender": str(sender.uuid),
            "origin": str(origin_node.uuid),
            "reach": new_reach.name,
            "filter_node": str(new_filter_node.uuid),
            "filter_node_feature": new_feature.slug,
            "filter_node_role": new_filter_role.name,
            "filter_current_relation": Node.Relation.DOWNSTREAM.value,
            "filter_installed_version": "2.0.0",
            "filter_installed_revision": "newrev",
        }
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = key.sign(
            payload_json.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        with patch.object(NetMessage, "propagate") as mock_propagate:
            response = self.client.post(
                reverse("net-message"),
                data=payload_json,
                content_type="application/json",
                HTTP_X_SIGNATURE=base64.b64encode(signature).decode(),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(
                NetMessage.objects.filter(uuid=msg_uuid).values_list(
                    "pk", flat=True
                )
            ),
            [original.pk],
        )
        original.refresh_from_db()
        self.assertEqual(original.subject, payload["subject"])
        self.assertEqual(original.body, payload["body"])
        self.assertEqual(original.reach, new_reach)
        self.assertEqual(original.node_origin, origin_node)
        self.assertEqual(original.filter_node, new_filter_node)
        self.assertEqual(original.filter_node_feature, new_feature)
        self.assertEqual(original.filter_node_role, new_filter_role)
        self.assertEqual(
            original.filter_current_relation, payload["filter_current_relation"]
        )
        self.assertEqual(
            original.filter_installed_version, payload["filter_installed_version"]
        )
        self.assertEqual(
            original.filter_installed_revision, payload["filter_installed_revision"]
        )
        mock_propagate.assert_called_once()
        self.assertEqual(
            mock_propagate.call_args.kwargs["seen"], payload["seen"]
        )

    def test_net_message_applies_attachments(self):
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
            hostname="sender-attachments",
            address="10.0.0.100",
            port=8050,
            mac_address="00:11:22:33:44:ab",
            public_key=public_key,
        )
        existing_role = NodeRole.objects.create(
            name="AttachmentExisting", description="old"
        )
        attachments = [
            {
                "model": "nodes.noderole",
                "fields": {
                    "name": "AttachmentNew",
                    "description": "created via attachment",
                },
            },
            {
                "model": "nodes.noderole",
                "pk": existing_role.pk,
                "fields": {
                    "name": existing_role.name,
                    "description": "updated via attachment",
                },
            },
        ]
        msg_uuid = uuid.uuid4()
        payload = {
            "uuid": str(msg_uuid),
            "subject": "attachments",
            "body": "process",
            "seen": [],
            "sender": str(sender.uuid),
            "origin": str(sender.uuid),
            "attachments": attachments,
        }
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = key.sign(
            payload_json.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        response = self.client.post(
            reverse("net-message"),
            data=payload_json,
            content_type="application/json",
            HTTP_X_SIGNATURE=base64.b64encode(signature).decode(),
        )
        self.assertEqual(response.status_code, 200)
        expected = NetMessage.normalize_attachments(attachments)
        message = NetMessage.objects.get(uuid=msg_uuid)
        self.assertEqual(message.attachments, expected)
        self.assertTrue(NodeRole.objects.filter(name="AttachmentNew").exists())
        existing_role.refresh_from_db()
        self.assertEqual(existing_role.description, "updated via attachment")

    @pytest.mark.feature("clipboard-poll")
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
        raw_name = f"poll_clipboard_node_{node.pk}"
        task_name = slugify_task_name(raw_name)
        PeriodicTask.objects.filter(
            name__in=periodic_task_name_variants(raw_name)
        ).delete()
        NodeFeatureAssignment.objects.create(node=node, feature=feature)
        self.assertTrue(PeriodicTask.objects.filter(name=task_name).exists())
        NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())

    @pytest.mark.feature("screenshot-poll")
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
        raw_name = f"capture_screenshot_node_{node.pk}"
        task_name = slugify_task_name(raw_name)
        PeriodicTask.objects.filter(
            name__in=periodic_task_name_variants(raw_name)
        ).delete()
        NodeFeatureAssignment.objects.create(node=node, feature=feature)
        self.assertTrue(PeriodicTask.objects.filter(name=task_name).exists())
        NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())

    def test_landing_lead_purge_task_syncs_with_celery_feature(self):
        feature, _ = NodeFeature.objects.get_or_create(
            slug="celery-queue", defaults={"display": "Celery Queue"}
        )
        node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": socket.gethostname(),
                "address": "127.0.0.1",
                "port": 9300,
                "base_path": settings.BASE_DIR,
            },
        )
        raw_name = "pages_purge_landing_leads"
        task_name = slugify_task_name(raw_name)
        PeriodicTask.objects.filter(
            name__in=periodic_task_name_variants(raw_name)
        ).delete()
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        self.assertTrue(
            PeriodicTask.objects.filter(name=task_name).exists()
        )
        NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
        self.assertFalse(
            PeriodicTask.objects.filter(name=task_name).exists()
        )

    def test_net_message_purge_task_syncs_with_feature(self):
        feature, _ = NodeFeature.objects.get_or_create(
            slug="celery-queue", defaults={"display": "Celery Queue"}
        )
        node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": socket.gethostname(),
                "address": "127.0.0.1",
                "port": 9320,
                "base_path": settings.BASE_DIR,
            },
        )
        raw_name = "nodes_purge_net_messages"
        task_name = slugify_task_name(raw_name)
        PeriodicTask.objects.filter(
            name__in=periodic_task_name_variants(raw_name)
        ).delete()
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        self.assertTrue(
            PeriodicTask.objects.filter(name=task_name).exists()
        )
        NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
        self.assertFalse(
            PeriodicTask.objects.filter(name=task_name).exists()
        )

    def test_node_update_task_syncs_with_feature(self):
        feature, _ = NodeFeature.objects.get_or_create(
            slug="celery-queue", defaults={"display": "Celery Queue"}
        )
        node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": socket.gethostname(),
                "address": "127.0.0.1",
                "port": 9330,
                "base_path": settings.BASE_DIR,
            },
        )
        raw_name = "nodes_update_all_information"
        task_name = slugify_task_name(raw_name)
        PeriodicTask.objects.filter(
            name__in=periodic_task_name_variants(raw_name)
        ).delete()

        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        task = PeriodicTask.objects.get(name=task_name)
        self.assertTrue(task.enabled)

        NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
        task.refresh_from_db()
        self.assertFalse(task.enabled)

    def test_update_all_nodes_information_uses_admin_workflow(self):
        feature, _ = NodeFeature.objects.get_or_create(
            slug="celery-queue", defaults={"display": "Celery Queue"}
        )
        local, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": socket.gethostname(),
                "address": "127.0.0.1",
                "port": 9340,
                "base_path": settings.BASE_DIR,
            },
        )
        remote = Node.objects.create(
            hostname="remote",
            address="10.0.0.2",
            port=9341,
            mac_address="00:11:22:33:44:bb",
        )
        NodeFeatureAssignment.objects.get_or_create(node=local, feature=feature)

        admin_stub = MagicMock()
        admin_stub._refresh_local_information.side_effect = [
            {"ok": True, "message": "local refreshed"},
            {"ok": False, "message": "remote unreachable"},
        ]
        admin_stub._push_remote_information.side_effect = [
            {"ok": True, "message": "local skip"},
            {"ok": True, "message": "remote updated"},
        ]

        with patch("nodes.tasks.Node.get_local", return_value=local), patch(
            "nodes.tasks._resolve_node_admin", return_value=admin_stub
        ):
            summary = update_all_nodes_information()

        self.assertEqual(admin_stub._refresh_local_information.call_count, 2)
        self.assertEqual(admin_stub._push_remote_information.call_count, 2)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["partial"], 1)
        self.assertEqual(summary["error"], 0)

        statuses = {entry["node_id"]: entry["status"] for entry in summary["results"]}
        self.assertEqual(statuses[local.pk], "success")
        self.assertEqual(statuses[remote.pk], "partial")

    def test_update_all_nodes_information_skips_without_feature(self):
        local = Node.objects.create(
            hostname="local",
            address="127.0.0.1",
            port=9350,
            mac_address=Node.get_current_mac(),
        )

        with patch("nodes.tasks.Node.get_local", return_value=local), patch(
            "nodes.tasks._resolve_node_admin"
        ) as resolve_admin:
            summary = update_all_nodes_information()

        resolve_admin.assert_not_called()
        self.assertTrue(summary.get("skipped"))
        self.assertEqual(summary["total"], 0)

    def test_ocpp_session_report_task_syncs_with_feature(self):
        feature, _ = NodeFeature.objects.get_or_create(
            slug="celery-queue", defaults={"display": "Celery Queue"}
        )
        node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": socket.gethostname(),
                "address": "127.0.0.1",
                "port": 9400,
                "base_path": settings.BASE_DIR,
            },
        )
        raw_name = "ocpp_send_daily_session_report"
        task_name = slugify_task_name(raw_name)
        PeriodicTask.objects.filter(
            name__in=periodic_task_name_variants(raw_name)
        ).delete()

        with patch("nodes.models.mailer.can_send_email", return_value=True):
            NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)

        self.assertTrue(PeriodicTask.objects.filter(name=task_name).exists())

        NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())

    def test_ocpp_session_report_task_requires_email(self):
        feature, _ = NodeFeature.objects.get_or_create(
            slug="celery-queue", defaults={"display": "Celery Queue"}
        )
        node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": socket.gethostname(),
                "address": "127.0.0.1",
                "port": 9500,
                "base_path": settings.BASE_DIR,
            },
        )
        raw_name = "ocpp_send_daily_session_report"
        task_name = slugify_task_name(raw_name)
        PeriodicTask.objects.filter(
            name__in=periodic_task_name_variants(raw_name)
        ).delete()

        with patch("nodes.models.mailer.can_send_email", return_value=False):
            NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)

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

    def _create_local_node(self):
        return Node.objects.create(
            hostname="localnode",
            address="127.0.0.1",
            port=8888,
            mac_address=Node.get_current_mac(),
        )

    def test_register_visitor_defaults_to_loopback_urls(self):
        response = self.client.get(reverse("admin:nodes_node_register_visitor"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(
            "const visitorInfoUrl = \"http://localhost:8888/nodes/info/\";",
            content,
        )
        self.assertIn(
            "const visitorRegisterUrl = \"http://localhost:8888/nodes/register/\";",
            content,
        )

    def test_register_visitor_accepts_custom_base_parameter(self):
        url = reverse("admin:nodes_node_register_visitor")
        cases = [
            ("example.com:9443", "http://example.com:9443"),
            ("https://visitor.example", "https://visitor.example"),
            ("[::1]:9000", "http://[::1]:9000"),
        ]
        for raw, expected_base in cases:
            with self.subTest(raw=raw):
                encoded = quote(raw, safe=":/[]")
                response = self.client.get(f"{url}?visitor={encoded}")
                self.assertEqual(response.status_code, 200)
                content = response.content.decode()
                self.assertIn(
                    f"const visitorInfoUrl = \"{expected_base}/nodes/info/\";",
                    content,
                )
                self.assertIn(
                    f"const visitorRegisterUrl = \"{expected_base}/nodes/register/\";",
                    content,
                )

    def test_node_feature_list_shows_default_action_when_enabled(self):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rfid-scanner", defaults={"display": "RFID Scanner"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        response = self.client.get(reverse("admin:nodes_nodefeature_changelist"))
        action_url = reverse("admin:core_rfid_scan")
        self.assertContains(response, f'href="{action_url}"')

    @pytest.mark.feature("rpi-camera")
    def test_node_feature_list_shows_all_actions_for_rpi_camera(self):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rpi-camera", defaults={"display": "Raspberry Pi Camera"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        response = self.client.get(reverse("admin:nodes_nodefeature_changelist"))
        snapshot_url = reverse("admin:nodes_nodefeature_take_snapshot")
        stream_url = reverse("admin:nodes_nodefeature_view_stream")
        self.assertContains(response, f'href="{snapshot_url}"')
        self.assertContains(response, f'href="{stream_url}"')

    @pytest.mark.feature("audio-capture")
    def test_node_feature_list_shows_microphone_action_when_enabled(self):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="audio-capture", defaults={"display": "Audio Capture"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        response = self.client.get(reverse("admin:nodes_nodefeature_changelist"))
        action_url = reverse("admin:nodes_nodefeature_test_microphone")
        self.assertContains(response, f'href="{action_url}"')

    @pytest.mark.feature("screenshot-poll")
    def test_node_feature_list_hides_default_action_when_disabled(self):
        self._create_local_node()
        NodeFeature.objects.get_or_create(
            slug="screenshot-poll", defaults={"display": "Screenshot Poll"}
        )
        response = self.client.get(reverse("admin:nodes_nodefeature_changelist"))
        action_url = reverse("admin:nodes_nodefeature_take_screenshot")
        self.assertNotContains(response, f'href="{action_url}"')

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
    def test_register_current_updates_existing_node(self):
        hostname = socket.gethostname()
        Node.objects.create(
            hostname=hostname,
            address="127.0.0.1",
            port=8888,
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

    def test_register_current_requires_superuser(self):
        User = get_user_model()
        staff = User.objects.create_user(
            username="staff", password="pass", is_staff=True
        )
        permission = Permission.objects.get(codename="view_node")
        staff.user_permissions.add(permission)
        self.client.force_login(staff)

        response = self.client.get(reverse("admin:nodes_node_register_current"))

        self.assertEqual(response.status_code, 403)

    def test_register_current_link_hidden_for_non_superusers(self):
        User = get_user_model()
        staff = User.objects.create_user(
            username="linkstaff", password="pass", is_staff=True
        )
        permission = Permission.objects.get(codename="view_node")
        staff.user_permissions.add(permission)
        self.client.force_login(staff)

        response = self.client.get(reverse("admin:nodes_node_changelist"))

        self.assertNotContains(
            response, reverse("admin:nodes_node_register_current")
        )

    def test_apply_remote_node_info_updates_role(self):
        terminal, _ = NodeRole.objects.get_or_create(name="Terminal")
        control, _ = NodeRole.objects.get_or_create(name="Control")
        node = Node.objects.create(
            hostname="remote-node",
            address="10.0.0.20",
            port=8001,
            mac_address="00:11:22:33:44:aa",
            role=terminal,
        )
        admin_instance = NodeAdmin(Node, admin.site)

        payload = {
            "hostname": node.hostname,
            "network_hostname": "remote-node.example.com",
            "address": node.address,
            "ipv4_address": "198.51.100.10",
            "ipv6_address": "2001:db8::10",
            "port": node.port,
            "role": "Control",
        }

        changed = admin_instance._apply_remote_node_info(node, payload)
        node.refresh_from_db()

        self.assertIn("role", changed)
        self.assertIn("network_hostname", changed)
        self.assertIn("ipv4_address", changed)
        self.assertIn("ipv6_address", changed)
        self.assertEqual(node.role, control)
        self.assertEqual(node.network_hostname, "remote-node.example.com")
        self.assertEqual(node.ipv4_address, "198.51.100.10")
        self.assertEqual(node.ipv6_address, "2001:db8::10")
        self.assertTrue(control.node_set.filter(pk=node.pk).exists())
        self.assertFalse(terminal.node_set.filter(pk=node.pk).exists())

    def test_apply_remote_node_info_accepts_role_name_key(self):
        terminal, _ = NodeRole.objects.get_or_create(name="Terminal")
        control, _ = NodeRole.objects.get_or_create(name="Control")
        node = Node.objects.create(
            hostname="role-name-node",
            address="10.0.0.21",
            port=8002,
            mac_address="00:11:22:33:44:bb",
            role=terminal,
        )
        admin_instance = NodeAdmin(Node, admin.site)

        payload = {
            "hostname": node.hostname,
            "network_hostname": "role-name-node.example.com",
            "address": node.address,
            "ipv4_address": "198.51.100.11",
            "ipv6_address": "2001:db8::11",
            "port": node.port,
            "role_name": "Control",
        }

        changed = admin_instance._apply_remote_node_info(node, payload)
        node.refresh_from_db()

        self.assertIn("role", changed)
        self.assertEqual(node.network_hostname, "role-name-node.example.com")
        self.assertEqual(node.ipv4_address, "198.51.100.11")
        self.assertEqual(node.ipv6_address, "2001:db8::11")
        self.assertEqual(node.role, control)
        self.assertTrue(control.node_set.filter(pk=node.pk).exists())
        self.assertFalse(terminal.node_set.filter(pk=node.pk).exists())

    def test_apply_remote_node_info_updates_version_and_ipv4(self):
        node = Node.objects.create(
            hostname="remote-version",
            address="203.0.113.50",
            port=9000,
            mac_address="00:11:22:33:44:cc",
            installed_version="1.0.0",
            installed_revision="rev-old",
        )
        admin_instance = NodeAdmin(Node, admin.site)

        payload = {
            "hostname": node.hostname,
            "address": node.address,
            "ipv4_address": "203.0.113.50,127.0.0.1,198.51.100.5",
            "installed_version": "2.0.0",
            "installed_revision": "deadbeef",
        }

        changed = admin_instance._apply_remote_node_info(node, payload)
        node.refresh_from_db()

        self.assertIn("installed_version", changed)
        self.assertIn("installed_revision", changed)
        self.assertIn("ipv4_address", changed)
        self.assertEqual(node.installed_version, "2.0.0")
        self.assertEqual(node.installed_revision, "deadbeef")
        self.assertEqual(node.ipv4_address, "203.0.113.50,198.51.100.5")

    @patch("nodes.admin.requests.post")
    def test_send_forwarding_metadata_retries_until_success(self, mock_post):
        request = RequestFactory().get("/")
        local_node = Node.objects.create(
            hostname="local",
            address="127.0.0.1",
            port=8888,
            mac_address="00:11:22:33:44:aa",
            public_key="LOCAL-PUB",
        )
        class DummyNode:
            def __init__(self):
                self.port = 8443
                self.urls: list[str] = []

            def iter_remote_urls(self, path: str):
                self.urls.append(path)
                yield "https://unreachable.example"
                yield "https://reachable.example"

            def __str__(self):  # pragma: no cover - trivial representation
                return "dummy-node"

        target = DummyNode()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        charger = Charger.objects.create(charger_id="FORWARD-1")

        failure = MagicMock()
        failure.ok = False
        failure.status_code = 404
        failure.json.return_value = {"detail": "not found"}
        success = MagicMock()
        success.ok = True
        success.status_code = 200
        success.json.return_value = {"status": "ok"}

        mock_post.side_effect = [failure, success]

        admin_instance = NodeAdmin(Node, admin.site)

        result = admin_instance._send_forwarding_metadata(
            request, target, [charger], local_node, private_key
        )

        self.assertTrue(result)
        self.assertEqual(
            target.urls, ["/nodes/network/chargers/forward/"]
        )
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(
            mock_post.call_args_list[1].args[0], "https://reachable.example"
        )

    @pytest.mark.feature("screenshot-poll")
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

    def test_visit_link_uses_local_admin_dashboard_for_local_node(self):
        node_admin = admin.site._registry[Node]
        local_node = self._create_local_node()

        link_html = node_admin.visit_link(local_node)

        self.assertIn(reverse("admin:index"), link_html)
        self.assertIn("target=\"_blank\"", link_html)

    def test_visit_link_prefers_remote_hostname_for_dashboard(self):
        node_admin = admin.site._registry[Node]
        remote = Node.objects.create(
            hostname="remote.example.com",
            address="198.51.100.20",
            port=8443,
            mac_address="aa:bb:cc:dd:ee:ff",
        )

        link_html = node_admin.visit_link(remote)

        self.assertIn("https://remote.example.com:8443/admin/", link_html)
        self.assertIn("target=\"_blank\"", link_html)

    def test_iter_remote_urls_handles_hostname_with_path_and_port(self):
        node_admin = admin.site._registry[Node]
        remote = SimpleNamespace(
            public_endpoint="",
            address="",
            hostname="example.com/interface",
            port=8443,
        )

        urls = list(node_admin._iter_remote_urls(remote, "/nodes/info/"))

        self.assertIn(
            "https://example.com:8443/interface/nodes/info/",
            urls,
        )
        self.assertIn(
            "http://example.com:8443/interface/nodes/info/",
            urls,
        )
        combined = "".join(urls)
        self.assertNotIn("interface:8443", combined)

    def test_iter_remote_urls_avoid_default_ports_when_non_standard(self):
        node = Node(
            hostname="remote.example.com",
            port=8888,
            public_endpoint="",
        )

        urls = list(node.iter_remote_urls("/nodes/net-message/"))

        self.assertIn(
            "https://remote.example.com:8888/nodes/net-message/",
            urls,
        )
        self.assertIn(
            "http://remote.example.com:8888/nodes/net-message/",
            urls,
        )
        self.assertNotIn(
            "https://remote.example.com/nodes/net-message/",
            urls,
        )
        self.assertNotIn(
            "http://remote.example.com/nodes/net-message/",
            urls,
        )


    @pytest.mark.feature("screenshot-poll")
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

    @pytest.mark.feature("screenshot-poll")
    @patch("nodes.admin.capture_screenshot")
    def test_take_screenshot_default_action_creates_sample(
        self, mock_capture_screenshot
    ):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="screenshot-poll", defaults={"display": "Screenshot Poll"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "default.png"
        file_path.write_bytes(b"default")
        mock_capture_screenshot.return_value = Path("screenshots/default.png")
        response = self.client.get(
            reverse("admin:nodes_nodefeature_take_screenshot"), follow=True
        )
        self.assertEqual(response.status_code, 200)
        sample = ContentSample.objects.get(kind=ContentSample.IMAGE)
        self.assertEqual(sample.node, node)
        self.assertEqual(sample.method, "DEFAULT_ACTION")
        mock_capture_screenshot.assert_called_once_with("http://testserver/")
        change_url = reverse("admin:nodes_contentsample_change", args=[sample.pk])
        self.assertEqual(response.redirect_chain[-1][0], change_url)

    def test_check_features_for_eligibility_action_success(self):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rfid-scanner", defaults={"display": "RFID Scanner"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        response = self.client.post(
            changelist_url,
            {
                "action": "check_features_for_eligibility",
                "_selected_action": [str(feature.pk)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "RFID Scanner is enabled on localnode. This feature cannot be enabled manually.",
            html=False,
        )
        self.assertContains(
            response, "Completed 1 of 1 feature check(s) successfully.", html=False
        )

    def test_check_features_for_eligibility_action_warns_when_disabled(self):
        self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rfid-scanner", defaults={"display": "RFID Scanner"}
        )
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        response = self.client.post(
            changelist_url,
            {
                "action": "check_features_for_eligibility",
                "_selected_action": [str(feature.pk)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "RFID Scanner is not enabled on localnode. This feature cannot be enabled manually.",
            html=False,
        )
        self.assertContains(
            response, "Completed 0 of 1 feature check(s) successfully.", html=False
        )

    @pytest.mark.feature("audio-capture")
    @patch("nodes.models.Node._has_audio_capture_device", return_value=False)
    def test_check_features_for_eligibility_audio_capture_requires_device(
        self, mock_device
    ):
        self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="audio-capture", defaults={"display": "Audio Capture"}
        )
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        response = self.client.post(
            changelist_url,
            {
                "action": "check_features_for_eligibility",
                "_selected_action": [str(feature.pk)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No audio recording device detected on localnode for Audio Capture. This feature can be enabled manually.",
            html=False,
        )
        self.assertContains(
            response, "Completed 0 of 1 feature check(s) successfully.", html=False
        )

    @pytest.mark.feature("screenshot-poll")
    def test_enable_selected_features_enables_manual_feature(self):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="screenshot-poll", defaults={"display": "Screenshot Poll"}
        )
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        response = self.client.post(
            changelist_url,
            {
                "action": "enable_selected_features",
                "_selected_action": [str(feature.pk)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Node.objects.get(pk=node.pk).has_feature("screenshot-poll"))
        self.assertContains(
            response, "Enabled 1 feature(s): Screenshot Poll", html=False
        )

    def test_enable_selected_features_warns_for_non_manual(self):
        self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rfid-scanner", defaults={"display": "RFID Scanner"}
        )
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        response = self.client.post(
            changelist_url,
            {
                "action": "enable_selected_features",
                "_selected_action": [str(feature.pk)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "RFID Scanner cannot be enabled manually.", html=False
        )
        self.assertContains(
            response,
            "None of the selected features can be enabled manually.",
            html=False,
        )

    @pytest.mark.feature("screenshot-poll")
    def test_take_screenshot_default_action_requires_enabled_feature(self):
        self._create_local_node()
        NodeFeature.objects.get_or_create(
            slug="screenshot-poll", defaults={"display": "Screenshot Poll"}
        )
        response = self.client.get(
            reverse("admin:nodes_nodefeature_take_screenshot"), follow=True
        )
        self.assertEqual(response.status_code, 200)
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        self.assertEqual(response.wsgi_request.path, changelist_url)
        self.assertEqual(ContentSample.objects.count(), 0)
        self.assertContains(response, "Screenshot Poll feature is not enabled")

    @pytest.mark.feature("rpi-camera")
    @patch("nodes.admin.capture_rpi_snapshot")
    def test_take_snapshot_default_action_creates_sample(self, mock_snapshot):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rpi-camera", defaults={"display": "Raspberry Pi Camera"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        camera_dir = settings.LOG_DIR / "camera"
        camera_dir.mkdir(parents=True, exist_ok=True)
        file_path = camera_dir / "snap.jpg"
        file_path.write_bytes(b"camera")
        mock_snapshot.return_value = file_path
        response = self.client.get(
            reverse("admin:nodes_nodefeature_take_snapshot"), follow=True
        )
        self.assertEqual(response.status_code, 200)
        sample = ContentSample.objects.get(kind=ContentSample.IMAGE)
        self.assertEqual(sample.node, node)
        self.assertEqual(sample.method, "RPI_CAMERA")
        change_url = reverse("admin:nodes_contentsample_change", args=[sample.pk])
        self.assertEqual(response.redirect_chain[-1][0], change_url)

    @pytest.mark.feature("rpi-camera")
    def test_view_stream_requires_enabled_feature(self):
        self._create_local_node()
        NodeFeature.objects.get_or_create(
            slug="rpi-camera", defaults={"display": "Raspberry Pi Camera"}
        )
        response = self.client.get(
            reverse("admin:nodes_nodefeature_view_stream"), follow=True
        )
        self.assertEqual(response.status_code, 200)
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        self.assertEqual(response.wsgi_request.path, changelist_url)
        self.assertContains(
            response, "Raspberry Pi Camera feature is not enabled on this node."
        )

    @pytest.mark.feature("rpi-camera")
    def test_view_stream_renders_when_feature_enabled(self):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rpi-camera", defaults={"display": "Raspberry Pi Camera"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        response = self.client.get(reverse("admin:nodes_nodefeature_view_stream"))
        self.assertEqual(response.status_code, 200)
        response.render()
        expected_stream = "http://testserver:8554/"
        self.assertEqual(response.context_data["stream_url"], expected_stream)
        self.assertEqual(response.context_data["stream_embed"], "iframe")
        self.assertContains(response, expected_stream)
        self.assertContains(response, "camera-stream__frame")

    @pytest.mark.feature("rpi-camera")
    def test_view_stream_uses_configured_stream_url(self):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rpi-camera", defaults={"display": "Raspberry Pi Camera"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        configured_stream = "https://camera.local/stream"
        with self.settings(RPI_CAMERA_STREAM_URL=configured_stream):
            response = self.client.get(
                reverse("admin:nodes_nodefeature_view_stream")
            )
        self.assertEqual(response.status_code, 200)
        response.render()
        self.assertEqual(response.context_data["stream_url"], configured_stream)
        self.assertEqual(response.context_data["stream_embed"], "iframe")
        self.assertContains(response, configured_stream)

    @pytest.mark.feature("rpi-camera")
    def test_view_stream_detects_mjpeg_stream(self):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rpi-camera", defaults={"display": "Raspberry Pi Camera"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        mjpeg_url = "http://camera.local/stream.mjpg"
        with self.settings(RPI_CAMERA_STREAM_URL=mjpeg_url):
            response = self.client.get(
                reverse("admin:nodes_nodefeature_view_stream")
            )
        self.assertEqual(response.status_code, 200)
        response.render()
        self.assertEqual(response.context_data["stream_embed"], "mjpeg")
        self.assertContains(response, "<img", html=False)

    @pytest.mark.feature("rpi-camera")
    def test_view_stream_marks_rtsp_stream_as_unsupported(self):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rpi-camera", defaults={"display": "Raspberry Pi Camera"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        rtsp_url = "rtsp://camera.local/stream"
        with self.settings(RPI_CAMERA_STREAM_URL=rtsp_url):
            response = self.client.get(
                reverse("admin:nodes_nodefeature_view_stream")
            )
        self.assertEqual(response.status_code, 200)
        response.render()
        self.assertEqual(response.context_data["stream_embed"], "unsupported")
        self.assertContains(response, "camera-stream__unsupported")

    @pytest.mark.feature("audio-capture")
    def test_test_microphone_requires_enabled_feature(self):
        self._create_local_node()
        NodeFeature.objects.get_or_create(
            slug="audio-capture", defaults={"display": "Audio Capture"}
        )
        response = self.client.get(
            reverse("admin:nodes_nodefeature_test_microphone"), follow=True
        )
        self.assertEqual(response.status_code, 200)
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        self.assertEqual(response.wsgi_request.path, changelist_url)
        self.assertContains(
            response, "Audio Capture feature is not enabled on this node."
        )

    @pytest.mark.feature("audio-capture")
    @patch("nodes.admin.Node._has_audio_capture_device", return_value=False)
    def test_test_microphone_requires_audio_device(
        self, mock_has_device
    ):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="audio-capture", defaults={"display": "Audio Capture"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        response = self.client.get(
            reverse("admin:nodes_nodefeature_test_microphone"), follow=True
        )
        self.assertEqual(response.status_code, 200)
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        self.assertEqual(response.wsgi_request.path, changelist_url)
        self.assertContains(
            response,
            "Audio Capture feature is enabled but no recording device was detected.",
        )
        mock_has_device.assert_called_once()

    @pytest.mark.feature("audio-capture")
    @patch("nodes.admin.save_audio_sample")
    @patch("nodes.admin.record_microphone_sample")
    @patch("nodes.admin.Node._has_audio_capture_device", return_value=True)
    def test_test_microphone_handles_capture_failure(
        self, mock_has_device, mock_record, mock_save
    ):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="audio-capture", defaults={"display": "Audio Capture"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        mock_record.side_effect = RuntimeError("Audio capture failed")

        response = self.client.get(
            reverse("admin:nodes_nodefeature_test_microphone"), follow=True
        )

        self.assertEqual(response.status_code, 200)
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        self.assertEqual(response.wsgi_request.path, changelist_url)
        self.assertContains(response, "Audio capture failed")
        mock_has_device.assert_called_once()
        mock_record.assert_called_once_with(duration_seconds=6)
        mock_save.assert_not_called()

    @pytest.mark.feature("audio-capture")
    @patch("nodes.admin.save_audio_sample", return_value=None)
    @patch("nodes.admin.record_microphone_sample", return_value=Path("/tmp/audio.wav"))
    @patch("nodes.admin.Node._has_audio_capture_device", return_value=True)
    def test_test_microphone_handles_duplicate_sample(
        self, mock_has_device, mock_record, mock_save
    ):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="audio-capture", defaults={"display": "Audio Capture"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)

        response = self.client.get(
            reverse("admin:nodes_nodefeature_test_microphone"), follow=True
        )

        self.assertEqual(response.status_code, 200)
        changelist_url = reverse("admin:nodes_nodefeature_changelist")
        self.assertEqual(response.wsgi_request.path, changelist_url)
        self.assertContains(response, "Duplicate audio sample; not saved")
        mock_has_device.assert_called_once()
        mock_record.assert_called_once_with(duration_seconds=6)
        mock_save.assert_called_once()

    @pytest.mark.feature("audio-capture")
    @patch("nodes.admin.save_audio_sample")
    @patch("nodes.admin.record_microphone_sample", return_value=Path("/tmp/audio.wav"))
    @patch("nodes.admin.Node._has_audio_capture_device", return_value=True)
    def test_test_microphone_redirects_to_sample(
        self, mock_has_device, mock_record, mock_save
    ):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="audio-capture", defaults={"display": "Audio Capture"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        sample = ContentSample.objects.create(
            path="audio/sample.wav", kind=ContentSample.AUDIO
        )
        mock_save.return_value = sample

        response = self.client.get(
            reverse("admin:nodes_nodefeature_test_microphone"), follow=False
        )

        change_url = reverse("admin:nodes_contentsample_change", args=[sample.pk])
        self.assertRedirects(response, change_url)
        mock_has_device.assert_called_once()
        mock_record.assert_called_once_with(duration_seconds=6)
        mock_save.assert_called_once()
    @patch("nodes.admin.requests.post")
    def test_import_rfids_action_fetches_and_imports(self, mock_post):
        local = self._create_local_node()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_bytes = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        security_dir = Path(settings.BASE_DIR) / "security"
        security_dir.mkdir(parents=True, exist_ok=True)
        (security_dir / f"{local.public_endpoint}").write_bytes(private_bytes)
        (security_dir / f"{local.public_endpoint}.pub").write_bytes(public_bytes)
        local.public_key = public_bytes.decode()
        local.save(update_fields=["public_key"])

        remote = Node.objects.create(
            hostname="remote",
            address="127.0.0.2",
            port=8010,
            mac_address="aa:bb:cc:dd:ee:ff",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "rfids": [
                {
                    "rfid": "abc123",
                    "custom_label": "Remote tag",
                    "key_a": "A1B2C3D4E5F6",
                    "key_b": "FFFFFFFFFFFF",
                    "data": ["sector"],
                    "key_a_verified": True,
                    "key_b_verified": False,
                    "allowed": True,
                    "color": RFID.BLACK,
                    "kind": RFID.CLASSIC,
                    "released": False,
                    "last_seen_on": None,
                }
            ]
        }
        mock_response.text = ""
        mock_post.return_value = mock_response

        response = self.client.post(
            reverse("admin:nodes_node_changelist"),
            {
                "action": "import_rfids_from_selected",
                "_selected_action": [str(remote.pk)],
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 200)
        response.render()

        self.assertTrue(RFID.objects.filter(rfid="ABC123").exists())
        tag = RFID.objects.get(rfid="ABC123")
        self.assertEqual(tag.custom_label, "Remote tag")
        self.assertEqual(tag.origin_node, remote)
        self.assertEqual(tag.data, ["sector"])

        results = response.context_data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "success")
        self.assertEqual(results[0]["created"], 1)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        payload = call_kwargs["data"]
        headers = call_kwargs["headers"]
        signature = base64.b64decode(headers["X-Signature"])
        key.public_key().verify(
            signature,
            payload.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    def test_create_charge_point_forwarder_action_creates_forwarder(self):
        local = self._create_local_node()
        remote = Node.objects.create(
            hostname="remote",
            address="127.0.0.2",
            port=8010,
            mac_address="aa:bb:cc:dd:ee:ff",
        )

        charger = Charger.objects.create(
            charger_id="FORWARD-CP-1",
            export_transactions=False,
            node_origin=local,
        )

        response = self.client.post(
            reverse("admin:nodes_node_changelist"),
            {
                "action": "create_charge_point_forwarder",
                "_selected_action": [str(remote.pk)],
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        forwarder = CPForwarder.objects.get(target_node=remote)
        self.assertFalse(forwarder.enabled)
        self.assertEqual(forwarder.source_node, local)
        self.assertEqual(forwarder.name, remote.hostname)

        charger.refresh_from_db()
        self.assertTrue(charger.export_transactions)
        self.assertIsNone(charger.forwarded_to)

    @patch("nodes.admin.requests.post")
    def test_import_rfids_links_existing_accounts_only(self, mock_post):
        local = self._create_local_node()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_bytes = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        security_dir = Path(settings.BASE_DIR) / "security"
        security_dir.mkdir(parents=True, exist_ok=True)
        (security_dir / f"{local.public_endpoint}").write_bytes(private_bytes)
        (security_dir / f"{local.public_endpoint}.pub").write_bytes(public_bytes)
        local.public_key = public_bytes.decode()
        local.save(update_fields=["public_key"])

        existing = CustomerAccount.objects.create(name="KNOWN")
        remote = Node.objects.create(
            hostname="remote", address="127.0.0.3", port=8020
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ""
        mock_response.json.return_value = {
            "rfids": [
                {
                    "rfid": "deadbeef",
                    "customer_accounts": [existing.pk, 9999],
                    "customer_account_names": ["KNOWN", "missing"],
                }
            ]
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            reverse("admin:nodes_node_changelist"),
            {
                "action": "import_rfids_from_selected",
                "_selected_action": [str(remote.pk)],
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 200)
        response.render()

        tag = RFID.objects.get(rfid="DEADBEEF")
        self.assertEqual(list(tag.energy_accounts.all()), [existing])
        self.assertEqual(CustomerAccount.objects.filter(name__iexact="missing").count(), 0)

        result = response.context_data["results"][0]
        self.assertEqual(result["status"], "partial")
        self.assertIn("9999", result["missing_accounts"])
        self.assertIn("missing", result["missing_accounts"])
        self.assertEqual(result["linked_accounts"], 1)

    @patch("nodes.admin.requests.post")
    def test_export_rfids_action_posts_payload(self, mock_post):
        local = self._create_local_node()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_bytes = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        security_dir = Path(settings.BASE_DIR) / "security"
        security_dir.mkdir(parents=True, exist_ok=True)
        (security_dir / f"{local.public_endpoint}").write_bytes(private_bytes)
        (security_dir / f"{local.public_endpoint}.pub").write_bytes(public_bytes)
        local.public_key = public_bytes.decode()
        local.save(update_fields=["public_key"])

        account = CustomerAccount.objects.create(name="LOCAL")
        tag = RFID.objects.create(rfid="1234ABCD", origin_node=local)
        tag.energy_accounts.add(account)

        remote = Node.objects.create(
            hostname="remote", address="127.0.0.4", port=8030
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ""
        mock_response.json.return_value = {
            "processed": 1,
            "created": 0,
            "updated": 1,
            "accounts_linked": 1,
            "missing_accounts": ["remote-missing"],
            "errors": 0,
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            reverse("admin:nodes_node_changelist"),
            {
                "action": "export_rfids_to_selected",
                "_selected_action": [str(remote.pk)],
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 200)
        response.render()

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        payload = call_kwargs["data"]
        headers = call_kwargs["headers"]
        signature = base64.b64decode(headers["X-Signature"])
        key.public_key().verify(
            signature,
            payload.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        payload_data = json.loads(payload)
        self.assertIn("rfids", payload_data)
        self.assertEqual(payload_data["rfids"][0]["rfid"], "1234ABCD")
        payload_rfid = payload_data["rfids"][0]
        self.assertEqual(payload_rfid["customer_accounts"], [account.pk])
        self.assertEqual(payload_rfid["energy_accounts"], [account.pk])

        result = response.context_data["results"][0]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["linked_accounts"], 1)
        self.assertIn("remote-missing", result["missing_accounts"])

    def test_update_selected_nodes_action_renders_progress_page(self):
        remote = Node.objects.create(
            hostname="remote", address="10.0.0.2", port=8010
        )
        response = self.client.post(
            reverse("admin:nodes_node_changelist"),
            {
                "action": "update_selected_nodes",
                "_selected_action": [str(remote.pk)],
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 200)
        response.render()
        self.assertContains(response, "Update selected nodes")
        self.assertIn(
            f'data-node-id="{remote.pk}"', response.content.decode()
        )
        self.assertContains(response, str(remote))

    def test_send_net_message_action_displays_form(self):
        target = Node.objects.create(
            hostname="remote-one", address="10.0.0.10", port=8020
        )
        response = self.client.post(
            reverse("admin:nodes_node_changelist"),
            {
                "action": "send_net_message",
                helpers.ACTION_CHECKBOX_NAME: [str(target.pk)],
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 200)
        response.render()
        self.assertContains(response, "Send Net Message")
        self.assertContains(response, str(target))
        self.assertContains(response, 'name="apply"')
        self.assertContains(response, "Selected node (1)")

    @patch("nodes.admin.NetMessage.propagate")
    def test_send_net_message_action_creates_messages(self, mock_propagate):
        first = Node.objects.create(
            hostname="remote-two", address="10.0.0.11", port=8021
        )
        second = Node.objects.create(
            hostname="remote-three", address="10.0.0.12", port=8022
        )
        url = reverse("admin:nodes_node_changelist")
        payload = {
            "action": "send_net_message",
            "apply": "1",
            helpers.ACTION_CHECKBOX_NAME: [str(first.pk), str(second.pk)],
            "subject": "Maintenance",
            "body": "We will reboot tonight.",
        }
        existing_ids = set(NetMessage.objects.values_list("pk", flat=True))
        response = self.client.post(url, payload, follow=True)
        self.assertEqual(response.status_code, 200)
        new_messages = NetMessage.objects.exclude(pk__in=existing_ids)
        self.assertEqual(new_messages.count(), 2)
        self.assertEqual(mock_propagate.call_count, 2)
        for node in (first, second):
            message = new_messages.get(filter_node=node)
            self.assertEqual(message.subject, "Maintenance")
            self.assertEqual(message.body, "We will reboot tonight.")
        self.assertContains(response, "Sent 2 net messages.")

    @patch("nodes.admin.requests.post")
    @patch("nodes.admin.requests.get")
    def test_update_selected_nodes_progress_updates_remote(
        self, mock_get, mock_post
    ):
        local = self._create_local_node()
        local.public_endpoint = "localnode"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_bytes = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        security_dir = Path(settings.BASE_DIR) / "security"
        security_dir.mkdir(parents=True, exist_ok=True)
        (security_dir / "localnode").write_bytes(private_bytes)
        (security_dir / "localnode.pub").write_bytes(public_bytes)
        local.public_key = public_bytes.decode()
        local.save(update_fields=["public_endpoint", "public_key"])

        remote = Node.objects.create(
            hostname="upstream", address="192.0.2.5", port=8100
        )

        get_response = MagicMock()
        get_response.ok = True
        get_response.status_code = 200
        get_response.reason = "OK"
        get_response.json.return_value = {
            "hostname": "upstream-updated",
            "address": "203.0.113.10",
            "port": 8200,
            "mac_address": "aa:bb:cc:dd:ee:ff",
            "public_key": "REMOTEKEY",
        }
        mock_get.return_value = get_response

        post_response = MagicMock()
        post_response.ok = True
        post_response.status_code = 200
        post_response.text = ""
        mock_post.return_value = post_response

        progress_url = reverse("admin:nodes_node_update_selected_progress")
        response = self.client.post(progress_url, {"node_id": remote.pk})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["local"]["ok"])
        self.assertTrue(payload["remote"]["ok"])

        remote.refresh_from_db()
        self.assertEqual(remote.hostname, "upstream-updated")
        self.assertEqual(remote.address, "203.0.113.10")
        self.assertEqual(remote.port, 8200)
        self.assertEqual(remote.mac_address, "aa:bb:cc:dd:ee:ff")
        self.assertEqual(remote.public_key, "REMOTEKEY")

        self.assertTrue(mock_get.called)
        self.assertIn("/nodes/info/", mock_get.call_args.args[0])
        self.assertTrue(mock_post.called)
        post_data = json.loads(mock_post.call_args.kwargs["data"])
        self.assertEqual(post_data["hostname"], local.hostname)
        self.assertEqual(post_data["mac_address"], local.mac_address)


class NodeProxyGatewayTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        public_key = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        self.node = Node.objects.create(
            hostname="requester",
            address="127.0.0.1",
            port=8888,
            mac_address="aa:bb:cc:dd:ee:aa",
            public_key=public_key,
        )
        patcher = patch("requests.post")
        self.addCleanup(patcher.stop)
        self.mock_requests_post = patcher.start()
        self.mock_requests_post.return_value = SimpleNamespace(
            ok=True,
            status_code=200,
            json=lambda: {},
            text="",
        )

    def tearDown(self):
        cache.clear()

    def _sign(self, payload):
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = base64.b64encode(
            self.private_key.sign(
                body.encode(), padding.PKCS1v15(), hashes.SHA256()
            )
        ).decode()
        return body, signature

    def test_proxy_session_creates_login_url(self):
        payload = {
            "requester": str(self.node.uuid),
            "user": {
                "username": "proxy-user",
                "email": "proxy@example.com",
                "first_name": "Proxy",
                "last_name": "User",
                "is_staff": True,
                "is_superuser": True,
                "groups": [],
                "permissions": [],
            },
            "target": "/admin/",
        }
        body, signature = self._sign(payload)
        response = self.client.post(
            reverse("node-proxy-session"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("login_url", data)
        user = get_user_model().objects.get(username="proxy-user")
        self.assertTrue(user.is_staff)
        parsed = urlparse(data["login_url"])
        login_response = self.client.get(parsed.path)
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response["Location"], "/admin/")
        self.assertEqual(self.client.session.get("_auth_user_id"), str(user.pk))
        second = self.client.get(parsed.path)
        self.assertEqual(second.status_code, 410)

    def test_proxy_session_accepts_mac_hint_when_uuid_unknown(self):
        payload = {
            "requester": str(uuid.uuid4()),
            "requester_mac": self.node.mac_address,
            "requester_public_key": self.node.public_key,
            "user": {
                "username": "proxy-user",
                "email": "proxy@example.com",
                "first_name": "Proxy",
                "last_name": "User",
                "is_staff": True,
                "is_superuser": True,
                "groups": [],
                "permissions": [],
            },
            "target": "/admin/",
        }
        body, signature = self._sign(payload)
        response = self.client.post(
            reverse("node-proxy-session"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)

    def test_proxy_execute_lists_nodes(self):
        Node.objects.create(
            hostname="target",
            address="127.0.0.5",
            port=8010,
            mac_address="aa:bb:cc:dd:ee:bb",
        )
        payload = {
            "requester": str(self.node.uuid),
            "action": "list",
            "model": "nodes.Node",
            "filters": {"hostname": "target"},
            "credentials": {
                "username": "suite-user",
                "password": "secret",
                "first_name": "Suite",
                "last_name": "User",
            },
        }
        body, signature = self._sign(payload)
        response = self.client.post(
            reverse("node-proxy-execute"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data.get("objects", [])), 1)
        record = data["objects"][0]
        self.assertEqual(record["fields"]["hostname"], "target")
        user = get_user_model().objects.get(username="suite-user")
        self.assertTrue(user.is_superuser)

    def test_proxy_execute_requires_valid_password_for_existing_user(self):
        User = get_user_model()
        User.objects.create_user(username="suite-user", password="correct")
        payload = {
            "requester": str(self.node.uuid),
            "action": "list",
            "model": "nodes.Node",
            "credentials": {
                "username": "suite-user",
                "password": "wrong",
            },
        }
        body, signature = self._sign(payload)
        response = self.client.post(
            reverse("node-proxy-execute"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 403)

    def test_proxy_execute_schema_returns_models(self):
        payload = {
            "requester": str(self.node.uuid),
            "action": "schema",
            "credentials": {
                "username": "suite-user",
                "password": "secret",
            },
        }
        body, signature = self._sign(payload)
        response = self.client.post(
            reverse("node-proxy-execute"),
            data=body,
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        models = data.get("models", [])
        self.assertTrue(models)
        suite_names = {entry.get("suite_name") for entry in models}
        self.assertIn("Nodes", suite_names)


class NodeRFIDAPITests(TestCase):
    def test_import_endpoint_applies_payload_without_creating_accounts(self):
        remote = Node.objects.create(
            hostname="remote", address="127.0.0.10", port=8050
        )
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        remote.public_key = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        remote.save(update_fields=["public_key"])

        existing = CustomerAccount.objects.create(name="KNOWN")

        payload = {
            "requester": str(remote.uuid),
            "rfids": [
                {
                    "rfid": "deadface",
                    "custom_label": "Remote",
                    "customer_accounts": [existing.pk, 777],
                    "customer_account_names": ["known", "missing"],
                }
            ],
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = base64.b64encode(
            key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA256())
        ).decode()

        response = self.client.post(
            reverse("node-rfid-import"),
            body,
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["created"], 1)
        self.assertEqual(data["updated"], 0)
        self.assertEqual(data["accounts_linked"], 1)
        self.assertIn("missing", data["missing_accounts"])
        self.assertIn("777", data["missing_accounts"])

        tag = RFID.objects.get(rfid="DEADFACE")
        self.assertEqual(tag.custom_label, "Remote")
        self.assertEqual(list(tag.energy_accounts.all()), [existing])
        self.assertEqual(
            CustomerAccount.objects.filter(name__iexact="missing").count(), 0
        )


class RFIDExportViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        NodeRole.objects.get_or_create(name="Terminal")
        self.local_node = Node.objects.create(
            hostname="local",
            address="127.0.0.1",
            port=8888,
            mac_address=Node.get_current_mac(),
        )
        self.remote_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.remote_public = (
            self.remote_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        self.remote_node = Node.objects.create(
            hostname="remote",
            address="10.0.0.2",
            port=8100,
            mac_address="00:11:22:33:44:55",
            public_key=self.remote_public,
        )

    def _sign_payload(self, payload):
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = self.remote_key.sign(
            payload_json.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return payload_json, base64.b64encode(signature).decode()

    def test_export_requires_signature(self):
        payload_json = json.dumps(
            {"requester": str(self.remote_node.uuid)},
            separators=(",", ":"),
            sort_keys=True,
        )
        response = self.client.post(
            reverse("node-rfid-export"),
            data=payload_json,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_export_returns_serialized_data(self):
        RFID.objects.create(rfid="ABCDEF")
        payload_json, signature = self._sign_payload(
            {"requester": str(self.remote_node.uuid)}
        )
        response = self.client.post(
            reverse("node-rfid-export"),
            data=payload_json,
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("rfids", body)
        self.assertEqual(len(body["rfids"]), 1)
        tag_data = body["rfids"][0]
        self.assertEqual(tag_data["rfid"], "ABCDEF")
        self.assertIn("custom_label", tag_data)


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

    def test_reply_action_prefills_initial_data(self):
        role = NodeRole.objects.get(name="Terminal")
        node = Node.objects.create(
            hostname="remote",
            address="10.0.0.10",
            port=8100,
            mac_address="00:11:22:33:44:55",
            role=role,
        )
        original = NetMessage.objects.create(
            subject="Ping",
            body="Hello",
            node_origin=node,
        )
        url = f"{reverse('admin:nodes_netmessage_add')}?reply_to={original.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        form = response.context_data["adminform"].form
        self.assertEqual(form["subject"].value(), "Re: Ping")
        self.assertEqual(str(form["filter_node"].value()), str(node.pk))

class NetMessageReachTests(TestCase):
    def setUp(self):
        self.roles = {}
        for name in ["Terminal", "Control", "Satellite", "Watchtower"]:
            self.roles[name], _ = NodeRole.objects.get_or_create(name=name)
        self.nodes = {}
        for idx, name in enumerate(
            ["Terminal", "Control", "Satellite", "Watchtower"], start=1
        ):
            self.nodes[name] = Node.objects.create(
                hostname=name.lower(),
                address=f"10.0.0.{idx}",
                port=8888 + idx,
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
    def test_watchtower_reach_prioritizes_watchtower(self, mock_post):
        msg = NetMessage.objects.create(
            subject="s", body="b", reach=self.roles["Watchtower"]
        )
        with patch.object(Node, "get_local", return_value=None):
            msg.propagate()
        roles = set(msg.propagated_to.values_list("role__name", flat=True))
        self.assertEqual(
            roles, {"Watchtower", "Satellite", "Control", "Terminal"}
        )
        self.assertEqual(mock_post.call_count, 4)

    @patch("requests.post")
    def test_default_reach_not_limited_to_terminal(self, mock_post):
        msg = NetMessage.objects.create(subject="s", body="b")
        with patch.object(Node, "get_local", return_value=None), patch(
            "random.shuffle", side_effect=lambda seq: None
        ):
            msg.propagate()
        roles = set(msg.propagated_to.values_list("role__name", flat=True))
        self.assertIn("Control", roles)
        self.assertEqual(mock_post.call_count, 4)


class NetMessageFilterTests(TestCase):
    def setUp(self):
        self.terminal_role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.control_role, _ = NodeRole.objects.get_or_create(name="Control")
        self.nodes = {
            "terminal": Node.objects.create(
                hostname="terminal-filter",
                address="10.20.0.1",
                port=8020,
                mac_address="00:11:22:33:55:01",
                role=self.terminal_role,
            ),
            "control": Node.objects.create(
                hostname="control-filter",
                address="10.20.0.2",
                port=8021,
                mac_address="00:11:22:33:55:02",
                role=self.control_role,
            ),
        }
        self.feature, _ = NodeFeature.objects.get_or_create(
            slug="filter-test", defaults={"display": "Filter Test"}
        )
        NodeFeatureAssignment.objects.get_or_create(
            node=self.nodes["control"], feature=self.feature
        )
        self.nodes["control"].current_relation = Node.Relation.UPSTREAM
        self.nodes["control"].installed_version = "1.2.3"
        self.nodes["control"].installed_revision = "abc123"
        self.nodes["control"].save(
            update_fields=[
                "current_relation",
                "installed_version",
                "installed_revision",
            ]
        )

    @patch("requests.post")
    def test_filter_node_limits_targets(self, mock_post):
        msg = NetMessage.objects.create(
            subject="s", body="b", filter_node=self.nodes["control"]
        )
        with patch.object(Node, "get_local", return_value=None):
            msg.propagate()
        self.assertEqual(
            list(msg.propagated_to.values_list("pk", flat=True)),
            [self.nodes["control"].pk],
        )
        mock_post.assert_called_once()

    @patch("requests.post")
    def test_filter_fields_limit_queryset(self, mock_post):
        msg = NetMessage.objects.create(
            subject="s",
            body="b",
            filter_node_feature=self.feature,
            filter_node_role=self.control_role,
            filter_current_relation=Node.Relation.UPSTREAM,
            filter_installed_version="1.2.3",
            filter_installed_revision="abc123",
        )
        with patch.object(Node, "get_local", return_value=None):
            msg.propagate()
        self.assertEqual(
            list(msg.propagated_to.values_list("pk", flat=True)),
            [self.nodes["control"].pk],
        )
        mock_post.assert_called_once()


class NetMessageBroadcastStringReachTests(TestCase):
    def test_broadcast_uses_role_lookup_for_string_reach(self):
        role = NodeRole.objects.create(name="Terminal")
        local = Node.objects.create(
            hostname="terminal-local",
            address="10.10.0.1",
            port=8010,
            mac_address="00:aa:bb:cc:dd:ff",
            role=role,
            public_endpoint="terminal-local",
        )
        seen = ["existing"]

        with patch.object(Node, "get_local", return_value=local), patch.object(
            NetMessage, "propagate"
        ) as mock_propagate:
            msg = NetMessage.broadcast(
                "Subject", "Body", reach="Terminal", seen=seen
            )

        self.assertEqual(msg.reach, role)
        self.assertEqual(msg.node_origin, local)
        mock_propagate.assert_called_once()
        called_args = mock_propagate.call_args
        self.assertIn("seen", called_args.kwargs)
        self.assertIs(called_args.kwargs["seen"], seen)

    def test_broadcast_applies_attachments(self):
        role = NodeRole.objects.create(name="Terminal")
        local = Node.objects.create(
            hostname="terminal-local",
            address="10.10.0.1",
            port=8010,
            mac_address="00:aa:bb:cc:dd:ff",
            role=role,
            public_endpoint="terminal-local",
        )
        attachments = [
            {
                "model": "nodes.noderole",
                "fields": {"name": "attachment-role", "description": "desc"},
            }
        ]
        expected = NetMessage.normalize_attachments(attachments)
        with (
            patch.object(Node, "get_local", return_value=local),
            patch.object(NetMessage, "propagate") as mock_propagate,
            patch.object(NetMessage, "apply_attachments") as mock_apply,
        ):
            msg = NetMessage.broadcast(
                "Subject",
                "Body",
                reach="Terminal",
                seen=None,
                attachments=attachments,
            )
        self.assertEqual(msg.attachments, expected)
        mock_apply.assert_called_once_with(expected)
        mock_propagate.assert_called_once_with(seen=[])


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
                    port=8888 + idx,
                    mac_address=f"00:11:22:33:44:{idx:02x}",
                    role=self.role,
                    public_endpoint=f"n{idx}",
                )
            )

    def test_broadcast_sets_node_origin(self):
        with patch.object(Node, "get_local", return_value=self.local):
            msg = NetMessage.broadcast(subject="subject", body="body")
        self.assertEqual(msg.node_origin, self.local)
        self.assertEqual(msg.reach, self.role)

    @patch("requests.post")
    @override_settings(NET_MESSAGE_DISABLE_PROPAGATION=True)
    @patch("core.notifications.notify", return_value=False)
    def test_propagate_skips_remote_when_disabled(
        self, mock_notify, mock_post
    ):
        msg = NetMessage.objects.create(subject="s", body="b", reach=self.role)
        with patch.object(Node, "get_local", return_value=self.local):
            msg.propagate()

        mock_notify.assert_called_once_with("s", "b")
        mock_post.assert_not_called()
        self.assertTrue(msg.complete)
        self.assertEqual(msg.propagated_to.count(), 0)

    @patch("requests.post")
    @patch("core.notifications.notify")
    def test_propagate_forwards_to_three_and_notifies_local(
        self, mock_notify, mock_post
    ):
        mock_post.return_value.ok = True
        mock_post.return_value.status_code = 200
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
    @patch("core.notifications.notify", return_value=False)
    def test_propagate_defaults_to_six_when_available(
        self, mock_notify, mock_post
    ):
        for idx in range(6, 12):
            self.remotes.append(
                Node.objects.create(
                    hostname=f"n{idx}",
                    address=f"10.0.0.{idx}",
                    port=8888 + idx,
                    mac_address=f"00:11:22:33:44:{idx:02x}",
                    role=self.role,
                    public_endpoint=f"n{idx}",
                )
            )
        msg = NetMessage.objects.create(subject="s", body="b", reach=self.role)
        with patch.object(Node, "get_local", return_value=self.local):
            msg.propagate()
        self.assertEqual(mock_post.call_count, 6)
        self.assertEqual(msg.propagated_to.count(), 6)
        self.assertFalse(msg.complete)

    @patch("requests.post")
    @patch("core.notifications.notify", return_value=False)
    def test_propagate_respects_target_limit(
        self, mock_notify, mock_post
    ):
        msg = NetMessage.objects.create(
            subject="s", body="b", reach=self.role, target_limit=2
        )
        with patch.object(Node, "get_local", return_value=self.local), patch(
            "random.shuffle", side_effect=lambda seq: None
        ):
            msg.propagate()

        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(msg.propagated_to.count(), 2)

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
            created=timezone.now() - timedelta(hours=25)
        )
        old_remote = NetMessage.objects.create(
            subject="old remote",
            body="body",
            reach=self.role,
            node_origin=self.remotes[0],
        )
        NetMessage.objects.filter(pk=old_remote.pk).update(
            created=timezone.now() - timedelta(hours=25)
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

    @patch("core.notifications.notify", return_value=False)
    def test_propagate_records_error_status(self, mock_notify):
        msg = NetMessage.objects.create(subject="s", body="b", reach=self.role)
        with (
            patch.object(Node, "get_local", return_value=self.local),
            patch("random.shuffle", side_effect=lambda seq: None),
            patch("requests.post", side_effect=Exception("boom")),
        ):
            msg.propagate()

        self.assertEqual(msg.propagated_to.count(), len(self.remotes))
        self.assertTrue(msg.complete)


class NetMessageQueueTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.feature, _ = NodeFeature.objects.get_or_create(
            slug="celery-queue", defaults={"display": "Celery Queue"}
        )

    def test_propagate_queues_unreachable_downstream(self):
        local = Node.objects.create(
            hostname="local",
            address="10.0.0.1",
            port=8888,
            mac_address="00:11:22:33:44:10",
            role=self.role,
            public_endpoint="local",
        )
        downstream = Node.objects.create(
            hostname="downstream",
            address="10.0.0.2",
            port=8001,
            mac_address="00:11:22:33:44:11",
            role=self.role,
            current_relation=Node.Relation.DOWNSTREAM,
        )
        message = NetMessage.objects.create(subject="Queued", body="Body", reach=self.role)
        with patch.object(Node, "get_local", return_value=local), patch.object(
            Node, "get_private_key", return_value=None
        ), patch("core.notifications.notify", return_value=False), patch(
            "requests.post", side_effect=Exception("fail")
        ):
            message.propagate()

        entry = PendingNetMessage.objects.get(node=downstream, message=message)
        self.assertIn(str(downstream.uuid), entry.seen)
        self.assertGreater(entry.stale_at, timezone.now())

    def test_queue_limit_enforced(self):
        downstream = Node.objects.create(
            hostname="limit",
            address="10.0.0.3",
            port=8002,
            mac_address="00:11:22:33:44:12",
            role=self.role,
            current_relation=Node.Relation.DOWNSTREAM,
            message_queue_length=1,
        )
        msg1 = NetMessage.objects.create(subject="Old", body="One", reach=self.role)
        msg2 = NetMessage.objects.create(subject="New", body="Two", reach=self.role)

        msg1.queue_for_node(downstream, [str(downstream.uuid)])
        msg2.queue_for_node(downstream, [str(downstream.uuid)])

        entries = list(PendingNetMessage.objects.filter(node=downstream))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].message, msg2)

    def test_queue_duplicate_updates_stale(self):
        downstream = Node.objects.create(
            hostname="dup",
            address="10.0.0.4",
            port=8003,
            mac_address="00:11:22:33:44:13",
            role=self.role,
            current_relation=Node.Relation.DOWNSTREAM,
        )
        message = NetMessage.objects.create(subject="Dup", body="Dup", reach=self.role)
        first = timezone.now()
        second = first + timedelta(minutes=5)
        with patch(
            "nodes.models.timezone.now", side_effect=[first, second, second]
        ):
            message.queue_for_node(downstream, ["first"])
            message.queue_for_node(downstream, ["second"])

        entry = PendingNetMessage.objects.get(node=downstream, message=message)
        self.assertEqual(entry.seen, ["second"])
        self.assertEqual(entry.queued_at, second)
        self.assertEqual(entry.stale_at, second + timedelta(hours=1))

    def test_pull_endpoint_returns_and_clears_messages(self):
        local_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        downstream_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        local = Node.objects.create(
            hostname="hub",
            address="10.0.0.5",
            port=8004,
            mac_address="00:11:22:33:44:14",
            role=self.role,
            public_endpoint="hub",
        )
        downstream = Node.objects.create(
            hostname="remote",
            address="10.0.0.6",
            port=8005,
            mac_address="00:11:22:33:44:15",
            role=self.role,
            current_relation=Node.Relation.DOWNSTREAM,
            public_key=downstream_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode(),
        )
        message = NetMessage.objects.create(subject="Fresh", body="Body", reach=self.role)
        stale_message = NetMessage.objects.create(subject="Stale", body="Body", reach=self.role)
        now = timezone.now()
        PendingNetMessage.objects.create(
            node=downstream,
            message=message,
            seen=[str(downstream.uuid)],
            stale_at=now + timedelta(minutes=30),
        )
        stale_entry = PendingNetMessage.objects.create(
            node=downstream,
            message=stale_message,
            seen=["stale"],
            stale_at=now - timedelta(minutes=5),
        )
        PendingNetMessage.objects.filter(pk=stale_entry.pk).update(
            queued_at=now - timedelta(minutes=5)
        )

        def fake_get_private(node_obj):
            if node_obj.pk == local.pk:
                return local_key
            return None

        payload = {"requester": str(downstream.uuid)}
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = base64.b64encode(
            downstream_key.sign(
                body.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        ).decode()

        with patch.object(Node, "get_local", return_value=local), patch.object(
            Node, "get_private_key", return_value=local_key
        ):
            response = self.client.post(
                reverse("net-message-pull"),
                data=body,
                content_type="application/json",
                HTTP_X_SIGNATURE=signature,
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data.get("messages", [])), 1)
        payload_data = data["messages"][0]["payload"]
        self.assertEqual(payload_data["uuid"], str(message.uuid))
        self.assertFalse(
            PendingNetMessage.objects.filter(node=downstream, message=message).exists()
        )
        self.assertFalse(
            PendingNetMessage.objects.filter(
                node=downstream, message=stale_message
            ).exists()
        )
        response_signature = data["messages"][0]["signature"]
        local_public = local_key.public_key()
        local_public.verify(
            base64.b64decode(response_signature),
            json.dumps(payload_data, separators=(",", ":"), sort_keys=True).encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    def test_poll_task_fetches_messages(self):
        local_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        upstream_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        local = Node.objects.create(
            hostname="downstream",
            address="10.0.0.7",
            port=8006,
            mac_address="00:11:22:33:44:16",
            role=self.role,
            public_endpoint="downstream",
        )
        upstream = Node.objects.create(
            hostname="upstream",
            address="127.0.0.2",
            port=8010,
            mac_address="00:11:22:33:44:17",
            role=self.role,
            current_relation=Node.Relation.UPSTREAM,
            public_key=upstream_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode(),
        )
        NodeFeatureAssignment.objects.create(node=local, feature=self.feature)
        payload = {
            "uuid": str(uuid.uuid4()),
            "subject": "Update",
            "body": "Body",
            "seen": [str(local.uuid)],
            "origin": str(upstream.uuid),
            "sender": str(upstream.uuid),
        }
        payload_text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        payload_signature = base64.b64encode(
            upstream_key.sign(
                payload_text.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        ).decode()
        response = MagicMock()
        response.ok = True
        response.json.return_value = {
            "messages": [{"payload": payload, "signature": payload_signature}]
        }

        with patch.object(Node, "get_local", return_value=local), patch.object(
            Node, "get_private_key", return_value=local_key
        ), patch("nodes.tasks.requests.post", return_value=response) as mock_post, patch.object(
            NetMessage, "propagate"
        ) as mock_propagate:
            poll_unreachable_upstream()

        created = NetMessage.objects.get(uuid=payload["uuid"])
        self.assertEqual(created.subject, "Update")
        self.assertEqual(created.node_origin, upstream)
        mock_post.assert_called_once()
        mock_propagate.assert_called_once()


class NetMessageMaintenanceTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")

    def test_purge_task_removes_old_messages_and_pending_entries(self):
        local = Node.objects.create(
            hostname="maintenance",
            address="10.0.0.20",
            port=8100,
            mac_address="00:11:22:33:44:30",
            role=self.role,
        )
        old_message = NetMessage.objects.create(subject="old", body="body", reach=self.role)
        recent_message = NetMessage.objects.create(subject="recent", body="body", reach=self.role)
        NetMessage.objects.filter(pk=old_message.pk).update(
            created=timezone.now() - timedelta(hours=30)
        )
        stale_entry = PendingNetMessage.objects.create(
            node=local,
            message=recent_message,
            seen=["stale"],
            stale_at=timezone.now() + timedelta(hours=1),
        )
        PendingNetMessage.objects.filter(pk=stale_entry.pk).update(
            queued_at=timezone.now() - timedelta(hours=30)
        )

        fresh_message = NetMessage.objects.create(
            subject="fresh pending", body="body", reach=self.role
        )
        remaining_entry = PendingNetMessage.objects.create(
            node=local,
            message=fresh_message,
            seen=["fresh"],
            stale_at=timezone.now() + timedelta(hours=2),
        )

        deleted = node_tasks.purge_stale_net_messages(24)

        self.assertFalse(NetMessage.objects.filter(pk=old_message.pk).exists())
        self.assertTrue(NetMessage.objects.filter(pk=recent_message.pk).exists())
        self.assertTrue(NetMessage.objects.filter(pk=fresh_message.pk).exists())
        self.assertFalse(PendingNetMessage.objects.filter(pk=stale_entry.pk).exists())
        self.assertTrue(PendingNetMessage.objects.filter(pk=remaining_entry.pk).exists())
        self.assertGreaterEqual(deleted, 1)


class ConstellationUdpKickstartTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")

    def test_kickstart_probes_constellation_peers(self):
        local = Node.objects.create(
            hostname="local-constellation",
            address="10.0.0.10",
            port=8888,
            mac_address="00:11:22:33:44:aa",
            public_endpoint="local-constellation",
            constellation_ip="10.88.0.10",
            role=self.role,
        )
        remote = Node.objects.create(
            hostname="remote-constellation",
            address="203.0.113.5",
            port=8890,
            mac_address="00:11:22:33:44:bb",
            public_endpoint="remote-constellation",
            constellation_ip="10.88.0.11",
            ipv4_address="198.51.100.4",
            role=self.role,
        )
        Node.objects.create(
            hostname="no-constellation",
            address="192.0.2.10",
            port=8891,
            mac_address="00:11:22:33:44:cc",
            public_endpoint="no-constellation",
            role=self.role,
        )

        remote.refresh_from_db()
        self.assertEqual(remote.constellation_ip, "10.88.0.11")
        self.assertTrue(
            Node.objects.filter(constellation_ip="10.88.0.11").exists()
        )
        self.assertEqual(
            list(
                Node.objects.exclude(constellation_ip__isnull=True)
                .values_list("constellation_ip", flat=True)
            ),
            ["10.88.0.10", "10.88.0.11"],
        )

        constellation_nodes = (
            Node.objects.filter(constellation_ip__isnull=False)
            .values_list("pk", flat=True)
        )
        self.assertIn(remote.pk, list(constellation_nodes))
        self.assertIn(remote.constellation_ip, remote.get_remote_host_candidates())

        send_calls: list[tuple[str, int]] = []

        def fake_probe(address: str, port: int) -> bool:
            send_calls.append((address, port))
            return True

        def fake_iter(node: Node) -> list[str]:
            self.assertEqual(node.pk, remote.pk)
            return [remote.constellation_ip]

        with (
            self.settings(CONSTELLATION_WG_PORT=53123),
            patch.object(Node, "get_local", return_value=local),
            patch(
                "nodes.tasks._synchronize_constellation_udp_window",
                return_value=1_700_000_000.0,
            ) as sync_mock,
            patch(
                "nodes.tasks._iter_constellation_probe_addresses",
                side_effect=fake_iter,
            ) as iter_mock,
            patch("nodes.tasks._send_udp_probe", side_effect=fake_probe) as probe_mock,
        ):
            result = kickstart_constellation_udp()

        self.assertGreaterEqual(sync_mock.call_count, 1)
        self.assertGreaterEqual(iter_mock.call_count, 1)
        self.assertGreaterEqual(probe_mock.call_count, 1)
        self.assertGreaterEqual(result, 1)

        targets = set(send_calls)
        self.assertIn((remote.constellation_ip, 53123), targets)
        self.assertNotIn((local.constellation_ip, 53123), targets)


    def test_udp_probe_window_waits_for_next_interval(self):
        time_values = iter([12.3, 15.0])
        sleep_calls: list[float] = []

        def fake_time() -> float:
            return next(time_values)

        def fake_sleep(duration: float) -> None:
            sleep_calls.append(duration)

        result = node_tasks._synchronize_constellation_udp_window(
            5, now_func=fake_time, sleep_func=fake_sleep
        )

        self.assertEqual(len(sleep_calls), 1)
        self.assertAlmostEqual(sleep_calls[0], 2.7, places=2)
        self.assertEqual(result, 15.0)

    def test_udp_probe_window_returns_immediately_when_aligned(self):
        def fake_time() -> float:
            return 60.0

        def fake_sleep(_duration: float) -> None:
            raise AssertionError("sleep should not be called when already aligned")

        result = node_tasks._synchronize_constellation_udp_window(
            30, now_func=fake_time, sleep_func=fake_sleep
        )

        self.assertEqual(result, 60.0)


class NetMessageSignatureTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.local = Node.objects.create(
            hostname="local",  # noqa: S106 - hostname in tests
            address="10.0.0.1",
            port=8001,
            mac_address="00:11:22:33:44:55",
            role=self.role,
            public_endpoint="local",
        )
        self.remote = Node.objects.create(
            hostname="remote",
            address="10.0.0.2",
            port=8002,
            mac_address="00:11:22:33:44:66",
            role=self.role,
            public_endpoint="remote",
        )

    def test_propagate_includes_signature_header(self):
        with TemporaryDirectory() as tmp:
            base_path = Path(tmp)
            security_dir = base_path / "security"
            security_dir.mkdir()
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem_data = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
            (security_dir / self.local.public_endpoint).write_bytes(pem_data)
            self.local.base_path = str(base_path)
            self.local.save(update_fields=["base_path"])

            captured_headers: list[dict[str, str]] = []

            def fake_post(url, data=None, headers=None, timeout=None):  # noqa: ARG001
                captured_headers.append(dict(headers or {}))
                return MagicMock()

            with (
                patch("core.notifications.notify", return_value=False),
                patch.object(Node, "get_local", return_value=self.local),
                patch("requests.post", side_effect=fake_post) as mock_post,
            ):
                msg_one = NetMessage.objects.create(
                    subject="sig",
                    body="first",
                    reach=self.role,
                    target_limit=1,
                )
                msg_one.propagate()

                msg_two = NetMessage.objects.create(
                    subject="sig",
                    body="second",
                    reach=self.role,
                    target_limit=1,
                )
                msg_two.propagate()

            self.assertEqual(mock_post.call_count, 2)

        self.assertGreaterEqual(len(captured_headers), 2)
        signature_one = captured_headers[0].get("X-Signature")
        signature_two = captured_headers[1].get("X-Signature")
        self.assertTrue(signature_one)
        self.assertTrue(signature_two)
        self.assertNotEqual(signature_one, signature_two)


class NetworkChargerActionSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.local_node = Node.objects.create(
            hostname="local-node",
            address="127.0.0.1",
            port=8888,
            mac_address="00:aa:bb:cc:dd:10",
            public_endpoint="local-endpoint",
        )
        self.authorized_node = Node.objects.create(
            hostname="authorized-node",
            address="127.0.0.2",
            port=8001,
            mac_address="00:aa:bb:cc:dd:11",
            public_endpoint="authorized-endpoint",
        )
        self.unauthorized_node, self.unauthorized_key = self._create_signed_node(
            "unauthorized-node",
            mac_suffix=0x12,
        )
        self.charger = Charger.objects.create(
            charger_id="SECURE-TEST-1",
            allow_remote=True,
            manager_node=self.authorized_node,
            node_origin=self.local_node,
        )

    def _create_signed_node(self, hostname: str, *, mac_suffix: int):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_bytes = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        node = Node.objects.create(
            hostname=hostname,
            address="10.0.0.{:d}".format(mac_suffix),
            port=8020,
            mac_address="00:aa:bb:cc:dd:{:02x}".format(mac_suffix),
            public_key=public_bytes.decode(),
            public_endpoint=f"{hostname}-endpoint",
        )
        return node, key

    def test_rejects_requests_from_unmanaged_nodes(self):
        url = reverse("node-network-charger-action")
        payload = {
            "requester": str(self.unauthorized_node.uuid),
            "charger_id": self.charger.charger_id,
            "action": "reset",
        }
        body = json.dumps(payload).encode()
        signature = self.unauthorized_key.sign(
            body,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        headers = {"HTTP_X_SIGNATURE": base64.b64encode(signature).decode()}

        with patch.object(Node, "get_local", return_value=self.local_node):
            response = self.client.post(
                url,
                data=body,
                content_type="application/json",
                **headers,
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json().get("detail"),
            "requester does not manage this charger",
        )


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

    def test_startup_notification_marks_nonrelease_version(self):
        from nodes.apps import _startup_notification

        package = Package.objects.create(name="pkg-start", is_active=False)
        PackageRelease.objects.create(
            package=package,
            version="1.2.3",
            revision="0" * 40,
        )

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "VERSION").write_text("1.2.3")
            with self.settings(BASE_DIR=tmp_path):
                with patch(
                    "nodes.apps.revision.get_revision", return_value="1" * 40
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
        self.assertEqual(kwargs["body"], "1.2.3+ r111111")


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

    def test_handler_skips_during_migrate_command(self):
        import sys

        from nodes.apps import _trigger_startup_notification

        with patch("nodes.apps._startup_notification") as mock_start:
            with patch.object(sys, "argv", ["manage.py", "migrate"]):
                _trigger_startup_notification()

        mock_start.assert_not_called()


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


@pytest.mark.feature("clipboard-poll")
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
            port=8888,
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
            port=8888,
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
            port=8888,
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

    def test_string_representation_trims_trailing_at_symbol(self):
        outbox = EmailOutbox.objects.create(
            host="smtp.example.com",
            port=587,
            username="mailer@",
            password="secret",
        )

        self.assertEqual(str(outbox), "mailer@smtp.example.com")

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
    @pytest.mark.feature("clipboard-poll")
    @patch("nodes.tasks.pyperclip.paste")
    def test_sample_clipboard_task_creates_sample(self, mock_paste):
        mock_paste.return_value = "task text"
        Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8888,
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

    @pytest.mark.feature("screenshot-poll")
    @patch("nodes.tasks.capture_screenshot")
    def test_capture_node_screenshot_task(self, mock_capture):
        node = Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8888,
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

    @pytest.mark.feature("screenshot-poll")
    @patch("nodes.tasks.capture_screenshot")
    def test_capture_node_screenshot_handles_error(self, mock_capture):
        Node.objects.create(
            hostname="host",
            address="127.0.0.1",
            port=8888,
            mac_address=Node.get_current_mac(),
        )
        mock_capture.side_effect = RuntimeError("boom")
        result = capture_node_screenshot("http://example.com")
        self.assertEqual(result, "")
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 0
        )


class CaptureScreenshotTests(TestCase):
    def setUp(self):
        super().setUp()
        self.firefox_patcher = patch(
            "nodes.utils._find_firefox_binary", return_value="/usr/bin/firefox"
        )
        self.ensure_geckodriver_patcher = patch("nodes.utils._ensure_geckodriver")
        self.firefox_patcher.start()
        self.ensure_geckodriver_patcher.start()
        self.addCleanup(self.firefox_patcher.stop)
        self.addCleanup(self.ensure_geckodriver_patcher.stop)

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

    def test_missing_firefox_reports_clear_error(self):
        with patch("nodes.utils._find_firefox_binary", return_value=None):
            with self.assertRaises(RuntimeError) as excinfo:
                capture_screenshot("http://example.com")
        self.assertIn("Firefox is not installed", str(excinfo.exception))

    @patch("nodes.utils.webdriver.Firefox")
    def test_driver_install_hint_on_failure(self, mock_firefox):
        mock_firefox.side_effect = WebDriverException("Unable to obtain driver for firefox")
        with self.assertRaises(RuntimeError) as excinfo:
            capture_screenshot("http://example.com")
        self.assertIn("Firefox WebDriver is unavailable", str(excinfo.exception))


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
            port=8888,
            mac_address="00:11:22:33:44:55",
            role=role,
        )
        node2 = Node.objects.create(
            hostname="n2",
            address="127.0.0.2",
            port=8888,
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
            port=8888,
            mac_address="00:11:22:33:44:77",
            role=role,
        )
        resp = self.client.get(reverse("admin:nodes_noderole_changelist"))
        self.assertContains(resp, '<td class="field-registered">1</td>', html=True)


class NodeFeatureFixtureTests(TestCase):
    def test_rfid_scanner_fixture_includes_control_role(self):
        for name in ("Terminal", "Satellite", "Watchtower", "Control"):
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

    @pytest.mark.feature("ap-router")
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

    @pytest.mark.feature("graphql")
    def test_graphql_fixture_excludes_terminal_role(self):
        for name in ("Control", "Interface", "Satellite", "Terminal", "Watchtower"):
            NodeRole.objects.get_or_create(name=name)
        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "node_features__nodefeature_graphql.json"
        )
        call_command("loaddata", str(fixture_path), verbosity=0)
        feature = NodeFeature.objects.get(slug="graphql")
        role_names = set(feature.roles.values_list("name", flat=True))
        self.assertEqual(
            role_names,
            {"Control", "Interface", "Satellite", "Watchtower"},
        )


class NodeFeatureTests(TestCase):
    def setUp(self):
        self.role, _ = NodeRole.objects.get_or_create(name="Terminal")
        with patch(
            "nodes.models.Node.get_current_mac", return_value="00:11:22:33:44:55"
        ):
            self.node = Node.objects.create(
                hostname="local",
                address="127.0.0.1",
                port=8888,
                mac_address="00:11:22:33:44:55",
                role=self.role,
            )

    def test_default_action_mapping_for_known_feature(self):
        feature = NodeFeature.objects.create(
            slug="rfid-scanner", display="RFID Scanner"
        )
        actions = feature.get_default_actions()
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.label, "Scan RFIDs")
        self.assertEqual(action.url_name, "admin:core_rfid_scan")
        self.assertEqual(feature.get_default_action(), action)

    def test_celery_feature_default_action(self):
        feature = NodeFeature.objects.create(
            slug="celery-queue", display="Celery Queue"
        )
        actions = feature.get_default_actions()
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.label, "Celery Report")
        self.assertEqual(action.url_name, "admin:nodes_nodefeature_celery_report")
        self.assertEqual(feature.get_default_action(), action)

    @pytest.mark.feature("rpi-camera")
    def test_rpi_camera_feature_has_multiple_actions(self):
        feature = NodeFeature.objects.create(
            slug="rpi-camera", display="Raspberry Pi Camera"
        )
        actions = feature.get_default_actions()
        self.assertEqual(len(actions), 2)
        labels = {action.label for action in actions}
        self.assertIn("Take a Snapshot", labels)
        self.assertIn("View stream", labels)

    @pytest.mark.feature("audio-capture")
    def test_audio_capture_feature_has_test_microphone_action(self):
        feature = NodeFeature.objects.create(
            slug="audio-capture", display="Audio Capture"
        )
        actions = feature.get_default_actions()
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.label, "Test Microphone")
        self.assertEqual(
            action.url_name, "admin:nodes_nodefeature_test_microphone"
        )

    def test_default_action_missing_when_unconfigured(self):
        feature = NodeFeature.objects.create(
            slug="custom-feature", display="Custom Feature"
        )
        self.assertEqual(feature.get_default_actions(), ())
        self.assertIsNone(feature.get_default_action())

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

    def test_feature_disabled_when_local_node_missing(self):
        feature = NodeFeature.objects.create(slug="lcd-screen", display="LCD")
        with patch("nodes.models.Node.get_local", return_value=None):
            with patch("core.notifications.supports_gui_toast") as mock_toast:
                self.assertFalse(feature.is_enabled)
        mock_toast.assert_not_called()

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

    @pytest.mark.feature("ap-router")
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
                port=8888,
                mac_address=mac,
                role=control_role,
            )
            node.refresh_features()
        self.assertTrue(
            NodeFeatureAssignment.objects.filter(node=node, feature=feature).exists()
        )

    @pytest.mark.feature("ap-router")
    @patch("nodes.models.Node._hosts_gelectriic_ap", return_value=True)
    def test_ap_router_detection_with_public_mode_lock(self, mock_hosts):
        control_role, _ = NodeRole.objects.get_or_create(name="Control")
        router = NodeFeature.objects.create(slug="ap-router", display="AP Router")
        router.roles.add(control_role)
        mac = "00:11:22:33:44:88"
        with TemporaryDirectory() as tmp, override_settings(BASE_DIR=Path(tmp)):
            locks = Path(tmp) / "locks"
            locks.mkdir(parents=True, exist_ok=True)
            (locks / "public_wifi_mode.lck").touch()
            with patch("nodes.models.Node.get_current_mac", return_value=mac):
                node = Node.objects.create(
                    hostname="control",
                    address="127.0.0.1",
                    port=8888,
                    mac_address=mac,
                    role=control_role,
                    base_path=str(Path(tmp)),
                )
                node.refresh_features()
        self.assertTrue(
            NodeFeatureAssignment.objects.filter(node=node, feature=router).exists()
        )

    @pytest.mark.feature("ap-router")
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
                port=8888,
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


class AudioCaptureDetectionTests(TestCase):
    def test_has_audio_capture_device_true(self):
        with TemporaryDirectory() as tmp:
            pcm_path = Path(tmp) / "pcm"
            pcm_path.write_text(
                "00-00: USB Audio : USB Audio : playback 1 : capture 1\n",
                encoding="utf-8",
            )
            with patch.object(Node, "AUDIO_CAPTURE_PCM_PATH", pcm_path):
                self.assertTrue(Node._has_audio_capture_device())

    def test_has_audio_capture_device_false_without_capture(self):
        with TemporaryDirectory() as tmp:
            pcm_path = Path(tmp) / "pcm"
            pcm_path.write_text(
                "00-00: USB Audio : USB Audio : playback 1\n",
                encoding="utf-8",
            )
            with patch.object(Node, "AUDIO_CAPTURE_PCM_PATH", pcm_path):
                self.assertFalse(Node._has_audio_capture_device())

    def test_has_audio_capture_device_false_when_file_missing(self):
        with TemporaryDirectory() as tmp:
            pcm_path = Path(tmp) / "pcm"
        with patch.object(Node, "AUDIO_CAPTURE_PCM_PATH", pcm_path):
            self.assertFalse(Node._has_audio_capture_device())


class AudioCaptureFeatureCheckTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(
            hostname="localnode",
            address="127.0.0.1",
            port=8888,
            mac_address=Node.get_current_mac(),
        )
        self.feature, _ = NodeFeature.objects.get_or_create(
            slug="audio-capture", defaults={"display": "Audio Capture"}
        )

    @pytest.mark.feature("audio-capture")
    @patch("nodes.models.Node._has_audio_capture_device", return_value=False)
    def test_feature_check_warns_without_device(self, mock_device):
        result = feature_checks.run(self.feature, node=self.node)
        self.assertFalse(result.success)
        self.assertIn("No audio recording device detected", result.message)
        self.assertEqual(result.level, messages.WARNING)

    @pytest.mark.feature("audio-capture")
    @patch("nodes.models.Node._has_audio_capture_device", return_value=True)
    def test_feature_check_warns_when_feature_disabled(self, mock_device):
        result = feature_checks.run(self.feature, node=self.node)
        self.assertFalse(result.success)
        self.assertIn("is not enabled", result.message)
        self.assertEqual(result.level, messages.WARNING)

    @pytest.mark.feature("audio-capture")
    @patch("nodes.models.Node._has_audio_capture_device", return_value=True)
    def test_feature_check_passes_when_enabled(self, mock_device):
        NodeFeatureAssignment.objects.get_or_create(node=self.node, feature=self.feature)
        result = feature_checks.run(self.feature, node=self.node)
        self.assertTrue(result.success)
        self.assertIn("recording device is available", result.message)
        self.assertEqual(result.level, messages.SUCCESS)



class CeleryReportAdminViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="secret"
        )
        self.client.force_login(self.superuser)

        self.log_file = Path(settings.LOG_DIR) / settings.LOG_FILE_NAME
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._original_log_contents: str | None = None
        if self.log_file.exists():
            self._original_log_contents = self.log_file.read_text(encoding="utf-8")
        self.addCleanup(self._restore_log_file)

        PeriodicTask.objects.all().delete()

    def _restore_log_file(self):
        if self._original_log_contents is None:
            try:
                self.log_file.unlink()
            except FileNotFoundError:
                pass
        else:
            self.log_file.write_text(
                self._original_log_contents, encoding="utf-8"
            )

    def test_report_includes_tasks_and_logs(self):
        now = timezone.now()
        schedule = IntervalSchedule.objects.create(
            every=1, period=IntervalSchedule.HOURS
        )
        PeriodicTask.objects.create(
            name="test-task",
            task="core.tasks.heartbeat",
            interval=schedule,
            enabled=True,
            last_run_at=now - timedelta(minutes=30),
        )

        localized = timezone.localtime(now)
        log_line = (
            f"{localized.strftime('%Y-%m-%d %H:%M:%S,%f')} "
            "[INFO] core.tasks: Heartbeat task executed\n"
        )
        self.log_file.write_text(log_line, encoding="utf-8")

        response = self.client.get(
            reverse("admin:nodes_nodefeature_celery_report")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Celery Report")
        self.assertContains(response, "test-task")
        self.assertContains(response, settings.LOG_FILE_NAME)
        entries = response.context_data["log_entries"]
        self.assertFalse(response.context_data["is_paginated"])
        self.assertTrue(
            any("Heartbeat task executed" in entry.message for entry in entries)
        )


    def test_report_includes_journal_entries(self):
        with patch("nodes.reports._collect_journal_entries") as mock_collect:
            entry = reports.CeleryLogEntry(
                timestamp=timezone.now(),
                level="INFO",
                logger="celery.worker",
                message="Worker processed journal task",
                source="systemd journal (celery-demo)",
            )
            mock_collect.return_value = ([entry], [entry.source])

            response = self.client.get(
                reverse("admin:nodes_nodefeature_celery_report")
            )

        self.assertEqual(response.status_code, 200)
        entries = response.context_data["log_entries"]
        self.assertTrue(
            any(item.message == entry.message and item.source == entry.source for item in entries)
        )
        self.assertIn(entry.source, response.context_data["log_sources"])


    def test_report_paginates_log_entries(self):
        now = timezone.now()
        localized = timezone.localtime(now)
        lines = []
        for index in range(150):
            timestamp = localized - timedelta(minutes=index)
            lines.append(
                f"{timestamp.strftime('%Y-%m-%d %H:%M:%S,%f')} "
                f"[INFO] celery.worker: Processed task {index}\n"
            )
        lines.reverse()
        self.log_file.write_text("".join(lines), encoding="utf-8")

        response = self.client.get(
            reverse("admin:nodes_nodefeature_celery_report")
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context_data["is_paginated"])
        page = response.context_data["log_page"]
        self.assertEqual(page.number, 1)
        self.assertEqual(len(response.context_data["log_entries"]), 100)
        self.assertTrue(page.has_next())

        response_page_2 = self.client.get(
            reverse("admin:nodes_nodefeature_celery_report"), {"page": 2}
        )

        self.assertEqual(response_page_2.status_code, 200)
        page_two = response_page_2.context_data["log_page"]
        self.assertEqual(page_two.number, 2)
        self.assertEqual(len(response_page_2.context_data["log_entries"]), 50)
        self.assertFalse(page_two.has_next())

class CeleryJournalCollectionTests(SimpleTestCase):
    def test_collect_journal_entries_returns_results(self):
        start = timezone.now() - timedelta(minutes=5)
        end = timezone.now()
        sample_entry = reports.CeleryLogEntry(
            timestamp=end - timedelta(minutes=1),
            level="INFO",
            logger="celery.worker",
            message="Processed journal task",
            source="systemd journal (celery-demo)",
        )

        with patch("nodes.reports._journalctl_available", return_value=True), patch(
            "nodes.reports._candidate_journal_units", return_value=["celery-demo"]
        ), patch(
            "nodes.reports._read_journal_entries",
            return_value=iter(
                [
                    reports.CeleryLogEntry(
                        timestamp=sample_entry.timestamp,
                        level=sample_entry.level,
                        logger=sample_entry.logger,
                        message=sample_entry.message,
                        source="celery-demo",
                    )
                ]
            ),
        ) as mock_read:
            entries, sources = reports._collect_journal_entries(
                start, end, max_lines=100
            )

        mock_read.assert_called_once()
        self.assertEqual(sources, [sample_entry.source])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].message, sample_entry.message)
        self.assertEqual(entries[0].source, sample_entry.source)

    def test_read_journal_entries_parses_json(self):
        start = timezone.now() - timedelta(minutes=5)
        end = timezone.now()
        record = {
            "__REALTIME_TIMESTAMP": str(int(end.timestamp() * 1_000_000)),
            "MESSAGE": "[INFO/MainProcess] Task example executed",
            "SYSLOG_IDENTIFIER": "celery",
            "PRIORITY": "6",
        }
        with patch(
            "nodes.reports.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout=json.dumps(record) + "\n",
                stderr="",
            ),
        ) as mock_run:
            entries = list(
                reports._read_journal_entries(
                    "celery-demo", start, end, max_lines=10
                )
            )

        mock_run.assert_called_once()
        self.assertEqual(len(entries), 1)
        self.assertIn("Task example executed", entries[0].message)
        self.assertEqual(entries[0].logger, "celery")

    def test_collect_journal_entries_handles_unavailable_tool(self):
        start = timezone.now() - timedelta(minutes=5)
        end = timezone.now()

        with patch("nodes.reports._journalctl_available", return_value=False):
            entries, sources = reports._collect_journal_entries(
                start, end, max_lines=50
            )

        self.assertEqual(entries, [])
        self.assertEqual(sources, [])


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


class DNSIntegrationTests(TestCase):
    def setUp(self):
        self.group = SecurityGroup.objects.create(name="Infra")

    def test_deploy_records_success(self):
        manager = NodeManager.objects.create(
            group=self.group,
            api_key="test-key",
            api_secret="test-secret",
        )
        record_a = DNSRecord.objects.create(
            domain="example.com",
            name="@",
            record_type=DNSRecord.Type.A,
            data="1.2.3.4",
            ttl=600,
        )
        record_b = DNSRecord.objects.create(
            domain="example.com",
            name="@",
            record_type=DNSRecord.Type.A,
            data="5.6.7.8",
            ttl=600,
        )

        calls = []

        class DummyResponse:
            status_code = 200
            reason = "OK"

            def json(self):
                return {}

        class DummySession:
            def __init__(self):
                self.headers = {}

            def put(self, url, json, timeout):
                calls.append((url, json, timeout, dict(self.headers)))
                return DummyResponse()

        with mock.patch.object(dns_utils.requests, "Session", DummySession):
            result = manager.publish_dns_records([record_a, record_b])

        self.assertEqual(len(result.deployed), 2)
        self.assertFalse(result.failures)
        self.assertFalse(result.skipped)
        self.assertTrue(calls)
        url, payload, timeout, headers = calls[0]
        self.assertTrue(url.endswith("/v1/domains/example.com/records/A/@"))
        self.assertEqual(len(payload), 2)
        self.assertEqual(headers["Authorization"], "sso-key test-key:test-secret")

        record_a.refresh_from_db()
        record_b.refresh_from_db()
        self.assertIsNotNone(record_a.last_synced_at)
        self.assertIsNotNone(record_b.last_synced_at)
        self.assertEqual(record_a.node_manager_id, manager.pk)
        self.assertEqual(record_b.node_manager_id, manager.pk)

    def test_deploy_records_handles_error(self):
        manager = NodeManager.objects.create(
            group=self.group,
            api_key="test-key",
            api_secret="test-secret",
        )
        record = DNSRecord.objects.create(
            domain="example.com",
            name="www",
            record_type=DNSRecord.Type.CNAME,
            data="target.example.com",
        )

        class DummyResponse:
            status_code = 400
            reason = "Bad Request"

            def json(self):
                return {"message": "Invalid data"}

        class DummySession:
            def __init__(self):
                self.headers = {}

            def put(self, url, json, timeout):
                return DummyResponse()

        with mock.patch.object(dns_utils.requests, "Session", DummySession):
            result = manager.publish_dns_records([record])

        self.assertFalse(result.deployed)
        self.assertIn(record, result.failures)
        record.refresh_from_db()
        self.assertEqual(record.last_error, "Invalid data")
        self.assertIsNone(record.last_synced_at)

    def test_validate_record_success(self):
        record = DNSRecord.objects.create(
            domain="example.com",
            name="www",
            record_type=DNSRecord.Type.A,
            data="1.2.3.4",
        )

        class DummyRdata:
            address = "1.2.3.4"

        class DummyResolver:
            def resolve(self, name, rtype):
                self_calls.append((name, rtype))
                return [DummyRdata()]

        self_calls = []
        ok, message = dns_utils.validate_record(record, resolver=DummyResolver())

        self.assertTrue(ok)
        self.assertEqual(message, "")
        record.refresh_from_db()
        self.assertIsNotNone(record.last_verified_at)
        self.assertEqual(record.last_error, "")
        self.assertEqual(self_calls, [("www.example.com", "A")])

    def test_validate_record_mismatch(self):
        record = DNSRecord.objects.create(
            domain="example.com",
            name="www",
            record_type=DNSRecord.Type.A,
            data="1.2.3.4",
        )

        class DummyRdata:
            address = "5.6.7.8"

        class DummyResolver:
            def resolve(self, name, rtype):
                return [DummyRdata()]

        ok, message = dns_utils.validate_record(record, resolver=DummyResolver())

        self.assertFalse(ok)
        self.assertEqual(message, "DNS record does not match expected value")
        record.refresh_from_db()
        self.assertEqual(record.last_error, message)
        self.assertIsNone(record.last_verified_at)

    def test_validate_record_handles_exception(self):
        record = DNSRecord.objects.create(
            domain="example.com",
            name="www",
            record_type=DNSRecord.Type.A,
            data="1.2.3.4",
        )

        class DummyResolver:
            def resolve(self, name, rtype):
                raise dns_resolver.NXDOMAIN()

        ok, message = dns_utils.validate_record(record, resolver=DummyResolver())

        self.assertFalse(ok)
        self.assertEqual(message, "The DNS query name does not exist.")
        record.refresh_from_db()
        self.assertEqual(record.last_error, message)
        self.assertIsNone(record.last_verified_at)


def fake_text_classifier(sample):
    content = sample.content or ""
    return [
        {"slug": "text-sample", "label": "Text Sample", "confidence": 0.75},
        {"slug": "shared-tag", "label": "Shared Tag", "metadata": {"length": len(content)}},
    ]


def fake_image_classifier(sample):
    return ["screenshot-tag"]


class ContentClassifierTests(TestCase):
    def setUp(self):
        ContentClassifier.objects.create(
            slug="text-classifier",
            label="Text Classifier",
            kind=ContentSample.TEXT,
            entrypoint="nodes.tests.fake_text_classifier",
            run_by_default=True,
            active=True,
        )
        ContentClassifier.objects.create(
            slug="image-classifier",
            label="Image Classifier",
            kind=ContentSample.IMAGE,
            entrypoint="nodes.tests.fake_image_classifier",
            run_by_default=True,
            active=True,
        )

    def test_save_screenshot_triggers_classifier(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            screenshot_path = base / "capture.png"
            screenshot_path.write_bytes(b"binary image data")
            with override_settings(LOG_DIR=base):
                sample = save_screenshot(screenshot_path, method="TEST")

        self.assertIsNotNone(sample)
        tags = ContentClassification.objects.filter(sample=sample)
        self.assertTrue(tags.filter(tag__slug="screenshot-tag").exists())

    def test_save_screenshot_returns_none_for_duplicate_without_linking(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            first_path = base / "capture.png"
            first_path.write_bytes(b"binary image data")
            duplicate_path = base / "duplicate.png"
            duplicate_path.write_bytes(b"binary image data")
            with override_settings(LOG_DIR=base):
                original = save_screenshot(first_path, method="TEST")
                duplicate = save_screenshot(duplicate_path, method="TEST")

        self.assertIsNotNone(original)
        self.assertIsNone(duplicate)
        self.assertEqual(ContentSample.objects.count(), 1)

    def test_save_screenshot_reuses_existing_sample_when_linking(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            first_path = base / "capture.png"
            first_path.write_bytes(b"binary image data")
            duplicate_path = base / "duplicate.png"
            duplicate_path.write_bytes(b"binary image data")
            with override_settings(LOG_DIR=base):
                original = save_screenshot(first_path, method="TEST")
                reused = save_screenshot(
                    duplicate_path, method="TEST", link_duplicates=True
                )

        self.assertIsNotNone(original)
        self.assertEqual(reused, original)
        self.assertEqual(ContentSample.objects.count(), 1)

    def test_text_sample_runs_default_classifiers_without_duplicates(self):
        sample = ContentSample.objects.create(
            content="Example content", kind=ContentSample.TEXT
        )

        self.assertEqual(sample.classifications.count(), 2)
        text_tag = sample.classifications.get(tag__slug="text-sample")
        self.assertAlmostEqual(text_tag.confidence, 0.75)
        shared_tag = sample.classifications.get(tag__slug="shared-tag")
        self.assertEqual(shared_tag.metadata, {"length": len(sample.content)})

        run_default_classifiers(sample)
        self.assertEqual(sample.classifications.count(), 2)


class CaptureRpiSnapshotTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        patcher = patch("nodes.utils.CAMERA_DIR", Path(self.tempdir.name))
        self.addCleanup(patcher.stop)
        patcher.start()

    @patch("pathlib.Path.exists", return_value=True)
    @patch("nodes.utils.subprocess.run")
    @patch("nodes.utils.shutil.which", return_value="/usr/bin/rpicam-still")
    @patch("nodes.utils.uuid.uuid4")
    @patch("nodes.utils.datetime")
    def test_snapshot_uses_unique_filenames(
        self,
        mock_datetime,
        mock_uuid,
        mock_which,
        mock_run,
        mock_exists,
    ):
        mock_datetime.utcnow.return_value = datetime(2024, 1, 1, 12, 0, 0)
        mock_uuid.side_effect = [
            uuid.UUID("00000000-0000-0000-0000-000000000000"),
            uuid.UUID("11111111-1111-1111-1111-111111111111"),
        ]
        mock_run.return_value = SimpleNamespace(returncode=0, stderr="", stdout="")

        first = capture_rpi_snapshot()
        second = capture_rpi_snapshot()

        self.assertNotEqual(first, second)
        self.assertTrue(first.name.endswith("-00000000000000000000000000000000.jpg"))
        self.assertTrue(second.name.endswith("-11111111111111111111111111111111.jpg"))
        self.assertEqual(mock_uuid.call_count, 2)
        self.assertEqual(mock_run.call_count, 2)
