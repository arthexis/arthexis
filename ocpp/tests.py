from asgiref.testing import ApplicationCommunicator
from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async
from django.test import Client, TransactionTestCase, TestCase, override_settings
from unittest import skip
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override, gettext as _
from django.contrib.sites.models import Site
from pages.models import Application, Module
from nodes.models import Node, NodeRole

from config.asgi import application

from .models import Transaction, Charger, Simulator, MeterReading, Location
from core.models import EnergyAccount, EnergyCredit
from core.models import RFID
from . import store
from django.db.models.deletion import ProtectedError
from decimal import Decimal
import json
import websockets
import asyncio
from pathlib import Path
from .simulator import SimulatorConfig, ChargePointSimulator
import re
from datetime import datetime, timedelta
from .tasks import purge_meter_readings
from django.db import close_old_connections
from django.db.utils import OperationalError
from urllib.parse import unquote, urlparse


class ClientWebsocketCommunicator(WebsocketCommunicator):
    """WebsocketCommunicator that injects a client address into the scope."""

    def __init__(
        self,
        application,
        path,
        *,
        client=None,
        headers=None,
        subprotocols=None,
        spec_version=None,
    ):
        if not isinstance(path, str):
            raise TypeError(f"Expected str, got {type(path)}")
        parsed = urlparse(path)
        scope = {
            "type": "websocket",
            "path": unquote(parsed.path),
            "query_string": parsed.query.encode("utf-8"),
            "headers": headers or [],
            "subprotocols": subprotocols or [],
        }
        if client is not None:
            scope["client"] = client
        if spec_version:
            scope["spec_version"] = spec_version
        self.scope = scope
        ApplicationCommunicator.__init__(self, application, self.scope)
        self.response_headers = None


class ChargerFixtureTests(TestCase):
    fixtures = [
        p.name
        for p in (Path(__file__).resolve().parent / "fixtures").glob(
            "initial_data__*.json"
        )
    ]

    @classmethod
    def setUpTestData(cls):
        location = Location.objects.create(name="Simulator")
        Charger.objects.create(
            charger_id="CP1",
            connector_id=1,
            location=location,
            require_rfid=False,
        )
        Charger.objects.create(
            charger_id="CP2",
            connector_id=2,
            location=location,
            require_rfid=True,
        )

    def test_cp2_requires_rfid(self):
        cp2 = Charger.objects.get(charger_id="CP2")
        self.assertTrue(cp2.require_rfid)

    def test_cp1_does_not_require_rfid(self):
        cp1 = Charger.objects.get(charger_id="CP1")
        self.assertFalse(cp1.require_rfid)

    def test_charger_connector_ids(self):
        cp1 = Charger.objects.get(charger_id="CP1")
        cp2 = Charger.objects.get(charger_id="CP2")
        self.assertEqual(cp1.connector_id, 1)
        self.assertEqual(cp2.connector_id, 2)
        self.assertEqual(cp1.name, "Simulator #1")
        self.assertEqual(cp2.name, "Simulator #2")


class ChargerUrlFallbackTests(TestCase):
    @override_settings(ALLOWED_HOSTS=["fallback.example", "10.0.0.0/8"])
    def test_reference_created_when_site_missing(self):
        Site.objects.all().delete()
        Site.objects.clear_cache()

        charger = Charger.objects.create(charger_id="NO_SITE")
        charger.refresh_from_db()

        self.assertIsNotNone(charger.reference)
        self.assertTrue(
            charger.reference.value.startswith("http://fallback.example")
        )
        self.assertTrue(charger.reference.value.endswith("/c/NO_SITE/"))


class SinkConsumerTests(TransactionTestCase):
    async def test_sink_replies(self):
        communicator = WebsocketCommunicator(application, "/ws/sink/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to([2, "1", "Foo", {}])
        response = await communicator.receive_json_from()
        self.assertEqual(response, [3, "1", {}])

        await communicator.disconnect()


class CSMSConsumerTests(TransactionTestCase):
    async def _retry_db(self, func, attempts: int = 5, delay: float = 0.1):
        """Run a database function, retrying if the database is locked."""
        for _ in range(attempts):
            try:
                return await database_sync_to_async(func)()
            except OperationalError:
                await database_sync_to_async(close_old_connections)()
                await asyncio.sleep(delay)
        raise

    async def test_transaction_saved(self):
        communicator = WebsocketCommunicator(application, "/TEST/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [
                2,
                "1",
                "StartTransaction",
                {"meterStart": 10, "connectorId": 3},
            ]
        )
        response = await communicator.receive_json_from()
        tx_id = response[2]["transactionId"]

        tx = await database_sync_to_async(Transaction.objects.get)(
            pk=tx_id, charger__charger_id="TEST"
        )
        self.assertEqual(tx.meter_start, 10)
        self.assertEqual(tx.connector_id, 3)
        self.assertIsNone(tx.stop_time)

        await communicator.send_json_to(
            [
                2,
                "2",
                "StopTransaction",
                {"transactionId": tx_id, "meterStop": 20},
            ]
        )
        await communicator.receive_json_from()

        await database_sync_to_async(tx.refresh_from_db)()
        self.assertEqual(tx.meter_stop, 20)
        self.assertIsNotNone(tx.stop_time)

        await communicator.disconnect()

    async def test_rfid_recorded(self):
        await database_sync_to_async(Charger.objects.create)(charger_id="RFIDREC")
        communicator = WebsocketCommunicator(application, "/RFIDREC/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [2, "1", "StartTransaction", {"meterStart": 1, "idTag": "TAG123"}]
        )
        response = await communicator.receive_json_from()
        tx_id = response[2]["transactionId"]

        tx = await database_sync_to_async(Transaction.objects.get)(
            pk=tx_id, charger__charger_id="RFIDREC"
        )
        self.assertEqual(tx.rfid, "TAG123")

        await communicator.disconnect()

    async def test_start_transaction_sends_net_message(self):
        location = await database_sync_to_async(Location.objects.create)(
            name="Test Location"
        )
        await database_sync_to_async(Charger.objects.create)(
            charger_id="NETMSG", location=location
        )
        communicator = WebsocketCommunicator(application, "/NETMSG/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        with patch("nodes.models.NetMessage.broadcast") as mock_broadcast:
            await communicator.send_json_to(
                [
                    2,
                    "1",
                    "StartTransaction",
                    {"meterStart": 1, "connectorId": 1},
                ]
            )
            await communicator.receive_json_from()

        await communicator.disconnect()

        mock_broadcast.assert_called_once()
        _, kwargs = mock_broadcast.call_args
        self.assertEqual(kwargs["subject"], "charging-started")
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["location"], "Test Location")
        self.assertEqual(payload["sn"], "NETMSG")
        self.assertEqual(payload["cid"], "1")

    async def test_rfid_unbound_instance_created(self):
        await database_sync_to_async(Charger.objects.create)(charger_id="NEWRFID")
        communicator = WebsocketCommunicator(application, "/NEWRFID/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [2, "1", "StartTransaction", {"meterStart": 1, "idTag": "TAG456"}]
        )
        await communicator.receive_json_from()

        tag = await database_sync_to_async(RFID.objects.get)(rfid="TAG456")
        count = await database_sync_to_async(tag.energy_accounts.count)()
        self.assertEqual(count, 0)

        await communicator.disconnect()

    async def test_firmware_status_notification_updates_database_and_views(self):
        communicator = WebsocketCommunicator(application, "/FWSTAT/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        ts = timezone.now().replace(microsecond=0)
        payload = {
            "status": "Installing",
            "statusInfo": "Applying patch",
            "timestamp": ts.isoformat(),
        }

        await communicator.send_json_to(
            [2, "1", "FirmwareStatusNotification", payload]
        )
        response = await communicator.receive_json_from()
        self.assertEqual(response, [3, "1", {}])

        def _fetch_status():
            charger = Charger.objects.get(charger_id="FWSTAT", connector_id=None)
            return (
                charger.firmware_status,
                charger.firmware_status_info,
                charger.firmware_timestamp,
            )

        status, info, recorded_ts = await database_sync_to_async(_fetch_status)()
        self.assertEqual(status, "Installing")
        self.assertEqual(info, "Applying patch")
        self.assertIsNotNone(recorded_ts)
        self.assertEqual(recorded_ts.replace(microsecond=0), ts)

        log_entries = store.get_logs(store.identity_key("FWSTAT", None), log_type="charger")
        self.assertTrue(
            any("FirmwareStatusNotification" in entry for entry in log_entries)
        )

        def _fetch_views():
            User = get_user_model()
            user = User.objects.create_user(username="fwstatus", password="pw")
            client = Client()
            client.force_login(user)
            detail = client.get(reverse("charger-detail", args=["FWSTAT"]))
            status_page = client.get(reverse("charger-status", args=["FWSTAT"]))
            list_response = client.get(reverse("charger-list"))
            return (
                detail.status_code,
                json.loads(detail.content.decode()),
                status_page.status_code,
                status_page.content.decode(),
                list_response.status_code,
                json.loads(list_response.content.decode()),
            )

        (
            detail_code,
            detail_payload,
            status_code,
            html,
            list_code,
            list_payload,
        ) = await database_sync_to_async(_fetch_views)()
        self.assertEqual(detail_code, 200)
        self.assertEqual(status_code, 200)
        self.assertEqual(list_code, 200)
        self.assertEqual(detail_payload["firmwareStatus"], "Installing")
        self.assertEqual(detail_payload["firmwareStatusInfo"], "Applying patch")
        self.assertEqual(detail_payload["firmwareTimestamp"], ts.isoformat())
        self.assertIn('id="firmware-status">Installing<', html)
        self.assertIn('id="firmware-status-info">Applying patch<', html)
        match = re.search(
            r'id="firmware-timestamp"[^>]*data-iso="([^"]+)"', html
        )
        self.assertIsNotNone(match)
        parsed_iso = datetime.fromisoformat(match.group(1))
        self.assertAlmostEqual(parsed_iso.timestamp(), ts.timestamp(), places=3)

        matching = [
            item
            for item in list_payload.get("chargers", [])
            if item["charger_id"] == "FWSTAT" and item["connector_id"] is None
        ]
        self.assertTrue(matching)
        self.assertEqual(matching[0]["firmwareStatus"], "Installing")
        self.assertEqual(matching[0]["firmwareStatusInfo"], "Applying patch")
        list_ts = datetime.fromisoformat(matching[0]["firmwareTimestamp"])
        self.assertAlmostEqual(list_ts.timestamp(), ts.timestamp(), places=3)

        store.clear_log(store.identity_key("FWSTAT", None), log_type="charger")

        await communicator.disconnect()

    async def test_firmware_status_notification_updates_connector_and_aggregate(
        self,
    ):
        communicator = WebsocketCommunicator(application, "/FWCONN/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [
                2,
                "1",
                "FirmwareStatusNotification",
                {"connectorId": 2, "status": "Downloaded"},
            ]
        )
        response = await communicator.receive_json_from()
        self.assertEqual(response, [3, "1", {}])

        def _fetch_chargers():
            aggregate = Charger.objects.get(charger_id="FWCONN", connector_id=None)
            connector = Charger.objects.get(charger_id="FWCONN", connector_id=2)
            return (
                aggregate.firmware_status,
                aggregate.firmware_status_info,
                aggregate.firmware_timestamp,
                connector.firmware_status,
                connector.firmware_status_info,
                connector.firmware_timestamp,
            )

        (
            aggregate_status,
            aggregate_info,
            aggregate_ts,
            connector_status,
            connector_info,
            connector_ts,
        ) = await database_sync_to_async(_fetch_chargers)()

        self.assertEqual(aggregate_status, "Downloaded")
        self.assertEqual(connector_status, "Downloaded")
        self.assertEqual(aggregate_info, "")
        self.assertEqual(connector_info, "")
        self.assertIsNotNone(aggregate_ts)
        self.assertIsNotNone(connector_ts)
        self.assertAlmostEqual(
            (connector_ts - aggregate_ts).total_seconds(), 0, delta=1.0
        )

        log_entries = store.get_logs(
            store.identity_key("FWCONN", 2), log_type="charger"
        )
        self.assertTrue(
            any("FirmwareStatusNotification" in entry for entry in log_entries)
        )
        log_entries_agg = store.get_logs(
            store.identity_key("FWCONN", None), log_type="charger"
        )
        self.assertTrue(
            any("FirmwareStatusNotification" in entry for entry in log_entries_agg)
        )

        store.clear_log(store.identity_key("FWCONN", 2), log_type="charger")
        store.clear_log(store.identity_key("FWCONN", None), log_type="charger")

        await communicator.disconnect()

    async def test_vin_recorded(self):
        await database_sync_to_async(Charger.objects.create)(charger_id="VINREC")
        communicator = WebsocketCommunicator(application, "/VINREC/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [2, "1", "StartTransaction", {"meterStart": 1, "vin": "WP0ZZZ11111111111"}]
        )
        response = await communicator.receive_json_from()
        tx_id = response[2]["transactionId"]

        tx = await database_sync_to_async(Transaction.objects.get)(
            pk=tx_id, charger__charger_id="VINREC"
        )
        self.assertEqual(tx.vin, "WP0ZZZ11111111111")

        await communicator.disconnect()

    async def test_connector_id_set_from_meter_values(self):
        communicator = WebsocketCommunicator(application, "/NEWCID/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        payload = {
            "connectorId": 7,
            "meterValue": [
                {
                    "timestamp": timezone.now().isoformat(),
                    "sampledValue": [{"value": "1"}],
                }
            ],
        }
        await communicator.send_json_to([2, "1", "MeterValues", payload])
        await communicator.receive_json_from()

        charger = await database_sync_to_async(Charger.objects.get)(
            charger_id="NEWCID", connector_id=7
        )
        self.assertEqual(charger.connector_id, 7)

        await communicator.disconnect()

    async def test_new_charger_created_for_different_connector(self):
        communicator = WebsocketCommunicator(application, "/DUPC/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        payload1 = {
            "connectorId": 1,
            "meterValue": [
                {
                    "timestamp": timezone.now().isoformat(),
                    "sampledValue": [{"value": "1"}],
                }
            ],
        }
        await communicator.send_json_to([2, "1", "MeterValues", payload1])
        await communicator.receive_json_from()
        await communicator.disconnect()
        await communicator.wait()
        await database_sync_to_async(close_old_connections)()

        communicator = WebsocketCommunicator(application, "/DUPC/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        payload2 = {
            "connectorId": 2,
            "meterValue": [
                {
                    "timestamp": timezone.now().isoformat(),
                    "sampledValue": [{"value": "1"}],
                }
            ],
        }
        await communicator.send_json_to([2, "1", "MeterValues", payload2])
        await communicator.receive_json_from()
        await communicator.disconnect()
        await communicator.wait()
        await database_sync_to_async(close_old_connections)()

        count = await self._retry_db(
            lambda: Charger.objects.filter(charger_id="DUPC").count()
        )
        self.assertEqual(count, 3)
        connectors = await self._retry_db(
            lambda: list(
                Charger.objects.filter(charger_id="DUPC").values_list(
                    "connector_id", flat=True
                )
            )
        )
        self.assertIn(1, connectors)
        self.assertIn(2, connectors)
        self.assertIn(None, connectors)

    async def test_transaction_created_from_meter_values(self):
        communicator = WebsocketCommunicator(application, "/NOSTART/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [
                2,
                "1",
                "MeterValues",
                {
                    "transactionId": 99,
                    "meterValue": [
                        {
                            "timestamp": "2025-01-01T00:00:00Z",
                            "sampledValue": [
                                {
                                    "value": "1000",
                                    "measurand": "Energy.Active.Import.Register",
                                    "unit": "W",
                                }
                            ],
                        }
                    ],
                },
            ]
        )
        await communicator.receive_json_from()

        tx = await database_sync_to_async(Transaction.objects.get)(
            pk=99, charger__charger_id="NOSTART"
        )
        self.assertEqual(tx.meter_start, 1000)
        self.assertIsNone(tx.meter_stop)

        await communicator.send_json_to(
            [
                2,
                "2",
                "StopTransaction",
                {"transactionId": 99, "meterStop": 1500},
            ]
        )
        await communicator.receive_json_from()
        await database_sync_to_async(tx.refresh_from_db)()
        self.assertEqual(tx.meter_stop, 1500)
        self.assertIsNotNone(tx.stop_time)

        await communicator.disconnect()

    async def test_temperature_recorded(self):
        charger = await database_sync_to_async(Charger.objects.create)(
            charger_id="TEMP1"
        )
        communicator = WebsocketCommunicator(application, "/TEMP1/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [
                2,
                "1",
                "MeterValues",
                {
                    "meterValue": [
                        {
                            "timestamp": "2025-01-01T00:00:00Z",
                            "sampledValue": [
                                {
                                    "value": "42",
                                    "measurand": "Temperature",
                                    "unit": "Celsius",
                                }
                            ],
                        }
                    ]
                },
            ]
        )
        await communicator.receive_json_from()

        await database_sync_to_async(charger.refresh_from_db)()
        self.assertEqual(charger.temperature, Decimal("42"))
        self.assertEqual(charger.temperature_unit, "Celsius")

        await communicator.disconnect()

    async def test_message_logged_and_session_file_created(self):
        cid = "LOGTEST1"
        log_path = Path("logs") / f"charger.{cid}.log"
        if log_path.exists():
            log_path.unlink()
        session_dir = Path("logs") / "sessions" / cid
        if session_dir.exists():
            for f in session_dir.glob("*.json"):
                f.unlink()
        communicator = WebsocketCommunicator(application, f"/{cid}/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [
                2,
                "1",
                "StartTransaction",
                {"meterStart": 1},
            ]
        )
        response = await communicator.receive_json_from()
        tx_id = response[2]["transactionId"]

        await communicator.send_json_to(
            [
                2,
                "2",
                "StopTransaction",
                {"transactionId": tx_id, "meterStop": 2},
            ]
        )
        await communicator.receive_json_from()
        await communicator.disconnect()

        content = log_path.read_text()
        self.assertIn("StartTransaction", content)
        self.assertNotIn(">", content)

        files = list(session_dir.glob(f"*_{tx_id}.json"))
        self.assertEqual(len(files), 1)
        data = json.loads(files[0].read_text())
        self.assertTrue(any("StartTransaction" in m["message"] for m in data))

    async def test_binary_message_logged(self):
        cid = "BINARY1"
        log_path = Path("logs") / f"charger.{cid}.log"
        if log_path.exists():
            log_path.unlink()
        communicator = WebsocketCommunicator(application, f"/{cid}/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_to(bytes_data=b"\x01\x02\x03")
        await communicator.disconnect()

        content = log_path.read_text()
        self.assertIn("AQID", content)

    async def test_session_file_written_on_disconnect(self):
        cid = "LOGTEST2"
        log_path = Path("logs") / f"charger.{cid}.log"
        if log_path.exists():
            log_path.unlink()
        session_dir = Path("logs") / "sessions" / cid
        if session_dir.exists():
            for f in session_dir.glob("*.json"):
                f.unlink()
        communicator = WebsocketCommunicator(application, f"/{cid}/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [
                2,
                "1",
                "StartTransaction",
                {"meterStart": 5},
            ]
        )
        await communicator.receive_json_from()

        await communicator.disconnect()

        session_dir = Path("logs") / "sessions" / cid
        files = list(session_dir.glob("*.json"))
        self.assertEqual(len(files), 1)
        data = json.loads(files[0].read_text())
        self.assertTrue(any("StartTransaction" in m["message"] for m in data))

    async def test_second_connection_closes_first(self):
        communicator1 = WebsocketCommunicator(application, "/DUPLICATE/")
        connected, _ = await communicator1.connect()
        self.assertTrue(connected)
        pending_key = store.pending_key("DUPLICATE")
        first_consumer = store.connections.get(pending_key)

        communicator2 = WebsocketCommunicator(application, "/DUPLICATE/")
        connected2, _ = await communicator2.connect()
        self.assertTrue(connected2)

        # The first communicator should be closed when the second connects.
        await communicator1.wait()
        self.assertIsNot(store.connections.get(pending_key), first_consumer)

        await communicator2.disconnect()

    async def test_connectors_share_serial_without_disconnecting(self):
        communicator1 = WebsocketCommunicator(application, "/MULTI/")
        connected1, _ = await communicator1.connect()
        self.assertTrue(connected1)
        await communicator1.send_json_to(
            [
                2,
                "1",
                "StartTransaction",
                {"connectorId": 1, "meterStart": 10},
            ]
        )
        await communicator1.receive_json_from()

        communicator2 = WebsocketCommunicator(application, "/MULTI/")
        connected2, _ = await communicator2.connect()
        self.assertTrue(connected2)
        await communicator2.send_json_to(
            [
                2,
                "2",
                "StartTransaction",
                {"connectorId": 2, "meterStart": 10},
            ]
        )
        await communicator2.receive_json_from()

        key1 = store.identity_key("MULTI", 1)
        key2 = store.identity_key("MULTI", 2)
        self.assertIn(key1, store.connections)
        self.assertIn(key2, store.connections)
        self.assertIsNot(store.connections[key1], store.connections[key2])

        await communicator1.disconnect()
        await communicator2.disconnect()
        store.transactions.pop(key1, None)
        store.transactions.pop(key2, None)


class ChargerLandingTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="u", password="pwd")
        self.client.force_login(self.user)

    def test_reference_created_and_page_renders(self):
        charger = Charger.objects.create(charger_id="PAGE1")
        self.assertIsNotNone(charger.reference)

        response = self.client.get(reverse("charger-page", args=["PAGE1"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["LANGUAGE_CODE"], "es")
        with override("es"):
            self.assertContains(
                response,
                _(
                    "Plug in your vehicle and slide your RFID card over the reader to begin charging."
                ),
            )
            self.assertContains(response, _("Advanced View"))
        status_url = reverse("charger-status-connector", args=["PAGE1", "all"])
        self.assertContains(response, status_url)

    def test_status_page_renders(self):
        charger = Charger.objects.create(charger_id="PAGE2")
        resp = self.client.get(reverse("charger-status", args=["PAGE2"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "PAGE2")

    def test_charger_page_shows_progress(self):
        charger = Charger.objects.create(charger_id="STATS")
        tx = Transaction.objects.create(
            charger=charger,
            meter_start=1000,
            start_time=timezone.now(),
        )
        key = store.identity_key(charger.charger_id, charger.connector_id)
        store.transactions[key] = tx
        resp = self.client.get(reverse("charger-page", args=["STATS"]))
        self.assertContains(resp, "progress-bar")
        store.transactions.pop(key, None)

    def test_display_name_used_on_public_pages(self):
        charger = Charger.objects.create(
            charger_id="NAMED",
            display_name="Entrada",
        )
        landing = self.client.get(reverse("charger-page", args=["NAMED"]))
        self.assertContains(landing, "Entrada")
        status = self.client.get(
            reverse("charger-status-connector", args=["NAMED", "all"])
        )
        self.assertContains(status, "Entrada")

    def test_total_includes_ongoing_transaction(self):
        charger = Charger.objects.create(charger_id="ONGOING")
        tx = Transaction.objects.create(
            charger=charger,
            meter_start=1000,
            start_time=timezone.now(),
        )
        key = store.identity_key(charger.charger_id, charger.connector_id)
        store.transactions[key] = tx
        MeterReading.objects.create(
            charger=charger,
            transaction=tx,
            timestamp=timezone.now(),
            measurand="Energy.Active.Import.Register",
            value=Decimal("2500"),
            unit="W",
        )
        resp = self.client.get(reverse("charger-status", args=["ONGOING"]))
        self.assertContains(resp, 'Total Energy: <span id="total-kw">1.50</span> kW')
        store.transactions.pop(key, None)

    def test_connector_specific_routes_render(self):
        Charger.objects.create(charger_id="ROUTED")
        connector = Charger.objects.create(charger_id="ROUTED", connector_id=1)
        page = self.client.get(
            reverse("charger-page-connector", args=["ROUTED", "1"])
        )
        self.assertEqual(page.status_code, 200)
        status = self.client.get(
            reverse("charger-status-connector", args=["ROUTED", "1"])
        )
        self.assertEqual(status.status_code, 200)
        search = self.client.get(
            reverse("charger-session-search-connector", args=["ROUTED", "1"])
        )
        self.assertEqual(search.status_code, 200)
        log_id = store.identity_key("ROUTED", connector.connector_id)
        store.add_log(log_id, "entry", log_type="charger")
        log = self.client.get(
            reverse("charger-log-connector", args=["ROUTED", "1"]) + "?type=charger"
        )
        self.assertContains(log, "entry")
        store.clear_log(log_id, log_type="charger")

    def test_temperature_displayed(self):
        charger = Charger.objects.create(
            charger_id="TEMP2", temperature=Decimal("21.5"), temperature_unit="Celsius"
        )
        resp = self.client.get(reverse("charger-status", args=["TEMP2"]))
        self.assertContains(resp, "Temperature")
        self.assertContains(resp, "21.5")

    def test_log_page_renders_without_charger(self):
        log_id = store.identity_key("LOG1", None)
        store.add_log(log_id, "hello", log_type="charger")
        entry = store.get_logs(log_id, log_type="charger")[0]
        self.assertRegex(entry, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} hello$")
        resp = self.client.get(reverse("charger-log", args=["LOG1"]) + "?type=charger")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "hello")
        store.clear_log(log_id, log_type="charger")

    def test_log_page_is_case_insensitive(self):
        log_id = store.identity_key("cp2", None)
        store.add_log(log_id, "entry", log_type="charger")
        resp = self.client.get(reverse("charger-log", args=["CP2"]) + "?type=charger")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "entry")
        store.clear_log(log_id, log_type="charger")


class SimulatorLandingTests(TestCase):
    def setUp(self):
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={"hostname": "localhost", "address": "127.0.0.1", "role": role},
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )
        app = Application.objects.create(name="Ocpp")
        module = Module.objects.create(node_role=role, application=app, path="/ocpp/")
        module.create_landings()
        User = get_user_model()
        self.user = User.objects.create_user(username="nav", password="pwd")
        self.client = Client()

    @skip("Navigation links unavailable in test environment")
    def test_simulator_app_link_in_nav(self):
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, "/ocpp/")
        self.assertNotContains(resp, "/ocpp/simulator/")
        self.client.force_login(self.user)
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, "/ocpp/")
        self.assertContains(resp, "/ocpp/simulator/")


class ChargerAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="ocpp-admin", password="secret", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_admin_lists_landing_link(self):
        charger = Charger.objects.create(charger_id="ADMIN1")
        url = reverse("admin:ocpp_charger_changelist")
        resp = self.client.get(url)
        self.assertContains(resp, charger.get_absolute_url())
        status_url = reverse(
            "charger-status-connector", args=["ADMIN1", "all"]
        )
        self.assertContains(resp, status_url)

    def test_admin_does_not_list_qr_link(self):
        charger = Charger.objects.create(charger_id="QR1")
        url = reverse("admin:ocpp_charger_changelist")
        resp = self.client.get(url)
        self.assertNotContains(resp, charger.reference.image.url)

    def test_admin_lists_log_link(self):
        charger = Charger.objects.create(charger_id="LOG1")
        url = reverse("admin:ocpp_charger_changelist")
        resp = self.client.get(url)
        log_url = reverse("admin:ocpp_charger_log", args=[charger.pk])
        self.assertContains(resp, log_url)

    def test_admin_log_view_displays_entries(self):
        charger = Charger.objects.create(charger_id="LOG2")
        log_id = store.identity_key(charger.charger_id, charger.connector_id)
        store.add_log(log_id, "entry", log_type="charger")
        url = reverse("admin:ocpp_charger_log", args=[charger.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "entry")
        store.clear_log(log_id, log_type="charger")

    def test_admin_change_links_landing_page(self):
        charger = Charger.objects.create(charger_id="CHANGE1")
        url = reverse("admin:ocpp_charger_change", args=[charger.pk])
        resp = self.client.get(url)
        self.assertContains(resp, charger.get_absolute_url())

    def test_admin_shows_location_name(self):
        loc = Location.objects.create(name="AdminLoc")
        Charger.objects.create(charger_id="ADMINLOC", location=loc)
        url = reverse("admin:ocpp_charger_changelist")
        resp = self.client.get(url)
        self.assertContains(resp, "AdminLoc")

    def test_last_fields_are_read_only(self):
        now = timezone.now()
        charger = Charger.objects.create(
            charger_id="ADMINRO",
            last_heartbeat=now,
            last_meter_values={"a": 1},
        )
        url = reverse("admin:ocpp_charger_change", args=[charger.pk])
        resp = self.client.get(url)
        self.assertContains(resp, "Last heartbeat")
        self.assertContains(resp, "Last meter values")
        self.assertNotContains(resp, 'name="last_heartbeat"')
        self.assertNotContains(resp, 'name="last_meter_values"')

    def test_purge_action_removes_data(self):
        charger = Charger.objects.create(charger_id="PURGE1")
        Transaction.objects.create(
            charger=charger,
            start_time=timezone.now(),
        )
        MeterReading.objects.create(
            charger=charger,
            timestamp=timezone.now(),
            value=1,
        )
        store.add_log(store.identity_key("PURGE1", None), "entry", log_type="charger")
        url = reverse("admin:ocpp_charger_changelist")
        self.client.post(
            url, {"action": "purge_data", "_selected_action": [charger.pk]}
        )
        self.assertFalse(Transaction.objects.filter(charger=charger).exists())
        self.assertFalse(MeterReading.objects.filter(charger=charger).exists())
        self.assertNotIn(
            store.identity_key("PURGE1", None), store.logs["charger"]
        )

    def test_delete_requires_purge(self):
        charger = Charger.objects.create(charger_id="DEL1")
        Transaction.objects.create(
            charger=charger,
            start_time=timezone.now(),
        )
        delete_url = reverse("admin:ocpp_charger_delete", args=[charger.pk])
        with self.assertRaises(ProtectedError):
            self.client.post(delete_url, {"post": "yes"})
        self.assertTrue(Charger.objects.filter(pk=charger.pk).exists())
        url = reverse("admin:ocpp_charger_changelist")
        self.client.post(
            url, {"action": "purge_data", "_selected_action": [charger.pk]}
        )
        self.client.post(delete_url, {"post": "yes"})
        self.assertFalse(Charger.objects.filter(pk=charger.pk).exists())


class LocationAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="loc-admin", password="secret", email="loc@example.com"
        )
        self.client.force_login(self.admin)

    def test_change_form_lists_related_chargers(self):
        location = Location.objects.create(name="LocAdmin")
        base = Charger.objects.create(charger_id="LOCBASE", location=location)
        connector = Charger.objects.create(
            charger_id="LOCALTWO",
            connector_id=1,
            location=location,
        )

        url = reverse("admin:ocpp_location_change", args=[location.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        base_change_url = reverse("admin:ocpp_charger_change", args=[base.pk])
        connector_change_url = reverse(
            "admin:ocpp_charger_change", args=[connector.pk]
        )

        self.assertContains(resp, base_change_url)
        self.assertContains(resp, connector_change_url)
        self.assertContains(resp, f"Charge Point: {base.charger_id}")
        self.assertContains(resp, f"Charge Point: {connector.charger_id} #1")

class TransactionAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="tx-admin", password="secret", email="tx@example.com"
        )
        self.client.force_login(self.admin)

    def test_meter_readings_inline_displayed(self):
        charger = Charger.objects.create(charger_id="T1")
        tx = Transaction.objects.create(charger=charger, start_time=timezone.now())
        reading = MeterReading.objects.create(
            charger=charger,
            transaction=tx,
            timestamp=timezone.now(),
            value=Decimal("2.123"),
            unit="kW",
        )
        url = reverse("admin:ocpp_transaction_change", args=[tx.pk])
        resp = self.client.get(url)
        self.assertContains(resp, str(reading.value))


class SimulatorAdminTests(TransactionTestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin2", password="secret", email="admin2@example.com"
        )
        self.client.force_login(self.admin)
        store.simulators.clear()
        store.logs["simulator"].clear()
        store.log_names["simulator"].clear()

    def test_admin_lists_log_link(self):
        sim = Simulator.objects.create(name="SIM", cp_path="SIMX")
        url = reverse("admin:ocpp_simulator_changelist")
        resp = self.client.get(url)
        log_url = reverse("admin:ocpp_simulator_log", args=[sim.pk])
        self.assertContains(resp, log_url)

    def test_admin_log_view_displays_entries(self):
        sim = Simulator.objects.create(name="SIMLOG", cp_path="SIMLOG")
        store.add_log("SIMLOG", "entry", log_type="simulator")
        url = reverse("admin:ocpp_simulator_log", args=[sim.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "entry")
        store.clear_log("SIMLOG", log_type="simulator")

    @patch("ocpp.admin.ChargePointSimulator.start")
    def test_start_simulator_message_includes_log_link(self, mock_start):
        sim = Simulator.objects.create(name="SIMMSG", cp_path="SIMMSG")
        mock_start.return_value = (True, "Connection accepted", "/tmp/sim.log")
        url = reverse("admin:ocpp_simulator_changelist")
        resp = self.client.post(
            url,
            {"action": "start_simulator", "_selected_action": [sim.pk]},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        log_url = reverse("admin:ocpp_simulator_log", args=[sim.pk])
        self.assertContains(resp, "View Log")
        self.assertContains(resp, log_url)
        self.assertContains(resp, "/tmp/sim.log")
        mock_start.assert_called_once()
        store.simulators.clear()

    def test_admin_shows_ws_url(self):
        sim = Simulator.objects.create(
            name="SIM2", cp_path="SIMY", host="h", ws_port=1111
        )
        url = reverse("admin:ocpp_simulator_changelist")
        resp = self.client.get(url)
        self.assertContains(resp, "ws://h:1111/SIMY/")

    def test_as_config_includes_custom_fields(self):
        sim = Simulator.objects.create(
            name="SIM3",
            cp_path="S3",
            interval=3.5,
            kw_max=70,
            duration=500,
            pre_charge_delay=5,
            vin="WP0ZZZ99999999999",
        )
        cfg = sim.as_config()
        self.assertEqual(cfg.interval, 3.5)
        self.assertEqual(cfg.kw_max, 70)
        self.assertEqual(cfg.duration, 500)
        self.assertEqual(cfg.pre_charge_delay, 5)
        self.assertEqual(cfg.vin, "WP0ZZZ99999999999")

    async def test_unknown_charger_auto_registered(self):
        communicator = WebsocketCommunicator(application, "/NEWCHG/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        exists = await database_sync_to_async(
            Charger.objects.filter(charger_id="NEWCHG").exists
        )()
        self.assertTrue(exists)

        charger = await database_sync_to_async(Charger.objects.get)(charger_id="NEWCHG")
        self.assertEqual(charger.last_path, "/NEWCHG/")

        await communicator.disconnect()

    async def test_nested_path_accepted_and_recorded(self):
        communicator = WebsocketCommunicator(application, "/foo/NEST/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.disconnect()

        charger = await database_sync_to_async(Charger.objects.get)(charger_id="NEST")
        self.assertEqual(charger.last_path, "/foo/NEST/")

    async def test_rfid_required_rejects_invalid(self):
        await database_sync_to_async(Charger.objects.create)(
            charger_id="RFID", require_rfid=True
        )
        communicator = WebsocketCommunicator(application, "/RFID/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [
                2,
                "1",
                "StartTransaction",
                {"meterStart": 0},
            ]
        )
        response = await communicator.receive_json_from()
        self.assertEqual(response[2]["idTagInfo"]["status"], "Invalid")

        exists = await database_sync_to_async(
            Transaction.objects.filter(charger__charger_id="RFID").exists
        )()
        self.assertFalse(exists)

        await communicator.disconnect()

    async def test_rfid_required_accepts_known_tag(self):
        User = get_user_model()
        user = await database_sync_to_async(User.objects.create_user)(
            username="bob", password="pwd"
        )
        acc = await database_sync_to_async(EnergyAccount.objects.create)(
            user=user, name="BOB"
        )
        await database_sync_to_async(EnergyCredit.objects.create)(
            account=acc, amount_kw=10
        )
        tag = await database_sync_to_async(RFID.objects.create)(rfid="CARDX")
        await database_sync_to_async(acc.rfids.add)(tag)
        await database_sync_to_async(Charger.objects.create)(
            charger_id="RFIDOK", require_rfid=True
        )
        communicator = WebsocketCommunicator(application, "/RFIDOK/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to(
            [
                2,
                "1",
                "StartTransaction",
                {"meterStart": 5, "idTag": "CARDX"},
            ]
        )
        response = await communicator.receive_json_from()
        self.assertEqual(response[2]["idTagInfo"]["status"], "Accepted")
        tx_id = response[2]["transactionId"]

        tx = await database_sync_to_async(Transaction.objects.get)(
            pk=tx_id, charger__charger_id="RFIDOK"
        )
        self.assertEqual(tx.account_id, user.energy_account.id)

    async def test_status_fields_updated(self):
        communicator = WebsocketCommunicator(application, "/STAT/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to([2, "1", "Heartbeat", {}])
        await communicator.receive_json_from()

        charger = await database_sync_to_async(Charger.objects.get)(charger_id="STAT")
        self.assertIsNotNone(charger.last_heartbeat)

        payload = {
            "meterValue": [
                {
                    "timestamp": "2025-01-01T00:00:00Z",
                    "sampledValue": [{"value": "42"}],
                }
            ]
        }
        await communicator.send_json_to([2, "2", "MeterValues", payload])
        await communicator.receive_json_from()

        await database_sync_to_async(charger.refresh_from_db)()
        self.assertEqual(
            charger.last_meter_values.get("meterValue")[0]["sampledValue"][0]["value"],
            "42",
        )

        await communicator.disconnect()


class ChargerLocationTests(TestCase):
    def test_lat_lon_fields_saved(self):
        loc = Location.objects.create(
            name="Loc1", latitude=10.123456, longitude=-20.654321
        )
        charger = Charger.objects.create(charger_id="LOC1", location=loc)
        self.assertAlmostEqual(float(charger.latitude), 10.123456)
        self.assertAlmostEqual(float(charger.longitude), -20.654321)
        self.assertEqual(charger.name, "Loc1")

    def test_location_created_when_missing(self):
        charger = Charger.objects.create(charger_id="AUTOLOC")
        self.assertIsNotNone(charger.location)
        self.assertEqual(charger.location.name, "AUTOLOC")

    def test_location_reused_for_matching_serial(self):
        first = Charger.objects.create(charger_id="SHARE", connector_id=1)
        first.location.name = "Custom"
        first.location.save()
        second = Charger.objects.create(charger_id="SHARE", connector_id=2)
        self.assertEqual(second.location, first.location)


class MeterReadingTests(TransactionTestCase):
    async def test_meter_values_saved_as_readings(self):
        communicator = WebsocketCommunicator(application, "/MR1/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        payload = {
            "connectorId": 1,
            "transactionId": 100,
            "meterValue": [
                {
                    "timestamp": "2025-07-29T10:01:51Z",
                    "sampledValue": [
                        {
                            "value": "2.749",
                            "measurand": "Energy.Active.Import.Register",
                            "unit": "kW",
                        }
                    ],
                }
            ],
        }
        await communicator.send_json_to([2, "1", "MeterValues", payload])
        await communicator.receive_json_from()

        reading = await database_sync_to_async(MeterReading.objects.get)(
            charger__charger_id="MR1"
        )
        self.assertEqual(reading.transaction_id, 100)
        self.assertEqual(str(reading.value), "2.749")
        tx = await database_sync_to_async(Transaction.objects.get)(
            pk=100, charger__charger_id="MR1"
        )
        self.assertEqual(tx.meter_start, 2749)

        await communicator.disconnect()


class ChargePointSimulatorTests(TransactionTestCase):
    async def test_simulator_sends_messages(self):
        received = []

        async def handler(ws):
            async for msg in ws:
                data = json.loads(msg)
                received.append(data)
                action = data[2]
                if action == "BootNotification":
                    await ws.send(
                        json.dumps(
                            [
                                3,
                                data[1],
                                {
                                    "status": "Accepted",
                                    "currentTime": "2024-01-01T00:00:00Z",
                                    "interval": 300,
                                },
                            ]
                        )
                    )
                elif action == "Authorize":
                    await ws.send(
                        json.dumps(
                            [
                                3,
                                data[1],
                                {"idTagInfo": {"status": "Accepted"}},
                            ]
                        )
                    )
                elif action == "StartTransaction":
                    await ws.send(
                        json.dumps(
                            [
                                3,
                                data[1],
                                {
                                    "transactionId": 1,
                                    "idTagInfo": {"status": "Accepted"},
                                },
                            ]
                        )
                    )
                elif action == "MeterValues":
                    await ws.send(json.dumps([3, data[1], {}]))
                elif action == "StopTransaction":
                    await ws.send(
                        json.dumps(
                            [
                                3,
                                data[1],
                                {"idTagInfo": {"status": "Accepted"}},
                            ]
                        )
                    )
                    break

        server = await websockets.serve(
            handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"]
        )
        port = server.sockets[0].getsockname()[1]

        try:
            cfg = SimulatorConfig(
                host="127.0.0.1",
                ws_port=port,
                cp_path="SIM1/",
                vin="WP0ZZZ12345678901",
                duration=0.2,
                interval=0.05,
                kw_min=0.1,
                kw_max=0.2,
                pre_charge_delay=0.0,
                serial_number="SN123",
                connector_id=7,
            )
            sim = ChargePointSimulator(cfg)
            await sim._run_session()
        finally:
            server.close()
            await server.wait_closed()

        actions = [msg[2] for msg in received]
        self.assertIn("BootNotification", actions)
        self.assertIn("StartTransaction", actions)
        boot_msg = next(msg for msg in received if msg[2] == "BootNotification")
        self.assertEqual(boot_msg[3].get("serialNumber"), "SN123")
        start_msg = next(msg for msg in received if msg[2] == "StartTransaction")
        self.assertEqual(start_msg[3].get("vin"), "WP0ZZZ12345678901")
        self.assertEqual(start_msg[3].get("connectorId"), 7)

    async def test_start_returns_status_and_log(self):
        async def handler(ws):
            async for msg in ws:
                data = json.loads(msg)
                action = data[2]
                if action == "BootNotification":
                    await ws.send(
                        json.dumps(
                            [
                                3,
                                data[1],
                                {
                                    "status": "Accepted",
                                    "currentTime": "2024-01-01T00:00:00Z",
                                    "interval": 300,
                                },
                            ]
                        )
                    )
                elif action == "Authorize":
                    await ws.send(
                        json.dumps([3, data[1], {"idTagInfo": {"status": "Accepted"}}])
                    )
                elif action == "StartTransaction":
                    await ws.send(
                        json.dumps(
                            [
                                3,
                                data[1],
                                {
                                    "transactionId": 1,
                                    "idTagInfo": {"status": "Accepted"},
                                },
                            ]
                        )
                    )
                elif action == "StopTransaction":
                    await ws.send(
                        json.dumps([3, data[1], {"idTagInfo": {"status": "Accepted"}}])
                    )
                    break
                else:
                    await ws.send(json.dumps([3, data[1], {}]))

        server = await websockets.serve(
            handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"]
        )
        port = server.sockets[0].getsockname()[1]

        cfg = SimulatorConfig(
            host="127.0.0.1",
            ws_port=port,
            cp_path="SIMSTART/",
            duration=0.1,
            interval=0.05,
            kw_min=0.1,
            kw_max=0.2,
            pre_charge_delay=0.0,
        )
        store.register_log_name(cfg.cp_path, "SimStart", log_type="simulator")
        try:
            sim = ChargePointSimulator(cfg)
            started, status, log_file = await asyncio.to_thread(sim.start)
            self.assertTrue(started)
            self.assertEqual(status, "Connection accepted")
            self.assertEqual(sim.status, "running")
            self.assertTrue(Path(log_file).exists())
        finally:
            await sim.stop()
            store.clear_log(cfg.cp_path, log_type="simulator")
            server.close()
            await server.wait_closed()

    async def test_simulator_stops_when_charger_closes(self):
        async def handler(ws):
            async for msg in ws:
                data = json.loads(msg)
                action = data[2]
                if action == "BootNotification":
                    await ws.send(json.dumps([3, data[1], {"status": "Accepted"}]))
                elif action == "Authorize":
                    await ws.send(
                        json.dumps([3, data[1], {"idTagInfo": {"status": "Accepted"}}])
                    )
                    await ws.close()
                    break

        server = await websockets.serve(
            handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"]
        )
        port = server.sockets[0].getsockname()[1]

        cfg = SimulatorConfig(
            host="127.0.0.1",
            ws_port=port,
            cp_path="SIMTERM/",
            duration=0.1,
            interval=0.05,
            kw_min=0.1,
            kw_max=0.2,
            pre_charge_delay=0.0,
        )
        sim = ChargePointSimulator(cfg)
        try:
            started, _, _ = await asyncio.to_thread(sim.start)
            self.assertTrue(started)
            # Allow time for the server to close the connection
            await asyncio.sleep(0.1)
            self.assertEqual(sim.status, "stopped")
            self.assertFalse(sim._thread.is_alive())
        finally:
            await sim.stop()
            server.close()
            await server.wait_closed()

    async def test_pre_charge_sends_heartbeat_and_meter(self):
        received = []

        async def handler(ws):
            async for msg in ws:
                data = json.loads(msg)
                received.append(data)
                action = data[2]
                if action == "BootNotification":
                    await ws.send(json.dumps([3, data[1], {"status": "Accepted"}]))
                elif action in {
                    "Authorize",
                    "StatusNotification",
                    "Heartbeat",
                    "MeterValues",
                }:
                    await ws.send(json.dumps([3, data[1], {}]))
                elif action == "StartTransaction":
                    await ws.send(
                        json.dumps(
                            [
                                3,
                                data[1],
                                {
                                    "transactionId": 1,
                                    "idTagInfo": {"status": "Accepted"},
                                },
                            ]
                        )
                    )
                elif action == "StopTransaction":
                    await ws.send(
                        json.dumps([3, data[1], {"idTagInfo": {"status": "Accepted"}}])
                    )
                    break

        server = await websockets.serve(
            handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"]
        )
        port = server.sockets[0].getsockname()[1]

        try:
            cfg = SimulatorConfig(
                host="127.0.0.1",
                ws_port=port,
                cp_path="SIMPRE/",
                duration=0.1,
                interval=0.05,
                kw_min=0.1,
                kw_max=0.2,
                pre_charge_delay=0.1,
            )
            sim = ChargePointSimulator(cfg)
            await sim._run_session()
        finally:
            server.close()
            await server.wait_closed()

        actions = [msg[2] for msg in received]
        start_idx = actions.index("StartTransaction")
        pre_actions = actions[:start_idx]
        self.assertIn("Heartbeat", pre_actions)
        self.assertIn("MeterValues", pre_actions)

    async def test_simulator_times_out_without_response(self):
        async def handler(ws):
            async for _ in ws:
                pass

        server = await websockets.serve(
            handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"]
        )
        port = server.sockets[0].getsockname()[1]

        cfg = SimulatorConfig(host="127.0.0.1", ws_port=port, cp_path="SIMTO/")
        sim = ChargePointSimulator(cfg)
        store.simulators[99] = sim
        try:

            async def fake_wait_for(coro, timeout):
                coro.close()
                raise asyncio.TimeoutError

            with patch("ocpp.simulator.asyncio.wait_for", fake_wait_for):
                started, status, _ = await asyncio.to_thread(sim.start)
            await asyncio.to_thread(sim._thread.join)
            self.assertFalse(started)
            self.assertIn("Timeout", status)
            self.assertNotIn(99, store.simulators)
        finally:
            await sim.stop()
            server.close()
            await server.wait_closed()


class PurgeMeterReadingsTaskTests(TestCase):
    def test_purge_old_meter_readings(self):
        charger = Charger.objects.create(charger_id="PURGER")
        tx = Transaction.objects.create(
            charger=charger,
            meter_start=0,
            meter_stop=1000,
            start_time=timezone.now(),
            stop_time=timezone.now(),
        )
        old = timezone.now() - timedelta(days=8)
        recent = timezone.now() - timedelta(days=2)
        MeterReading.objects.create(
            charger=charger, transaction=tx, timestamp=old, value=1
        )
        MeterReading.objects.create(
            charger=charger, transaction=tx, timestamp=recent, value=2
        )

        purge_meter_readings()

        self.assertEqual(MeterReading.objects.count(), 1)
        self.assertTrue(
            MeterReading.objects.filter(
                timestamp__gte=recent - timedelta(minutes=1)
            ).exists()
        )
        self.assertTrue(Transaction.objects.filter(pk=tx.pk).exists())

    def test_purge_skips_open_transactions(self):
        charger = Charger.objects.create(charger_id="PURGER2")
        tx = Transaction.objects.create(
            charger=charger,
            meter_start=0,
            start_time=timezone.now() - timedelta(days=9),
        )
        old = timezone.now() - timedelta(days=8)
        reading = MeterReading.objects.create(
            charger=charger, transaction=tx, timestamp=old, value=1
        )

        purge_meter_readings()

        self.assertTrue(MeterReading.objects.filter(pk=reading.pk).exists())


class TransactionKwTests(TestCase):
    def test_kw_sums_meter_readings(self):
        charger = Charger.objects.create(charger_id="SUM1")
        tx = Transaction.objects.create(
            charger=charger, start_time=timezone.now(), meter_start=0
        )
        MeterReading.objects.create(
            charger=charger,
            transaction=tx,
            timestamp=timezone.now(),
            value=Decimal("1000"),
            unit="W",
        )
        MeterReading.objects.create(
            charger=charger,
            transaction=tx,
            timestamp=timezone.now(),
            value=Decimal("1500"),
            unit="W",
        )
        self.assertAlmostEqual(tx.kw, 1.5)

    def test_kw_defaults_to_zero(self):
        charger = Charger.objects.create(charger_id="SUM2")
        tx = Transaction.objects.create(charger=charger, start_time=timezone.now())
        self.assertEqual(tx.kw, 0.0)


class ChargerStatusViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="status", password="pwd")
        self.client.force_login(self.user)

    def test_chart_data_populated_from_existing_readings(self):
        charger = Charger.objects.create(charger_id="VIEW1", connector_id=1)
        tx = Transaction.objects.create(
            charger=charger, start_time=timezone.now(), meter_start=0
        )
        t0 = timezone.now()
        MeterReading.objects.create(
            charger=charger,
            transaction=tx,
            timestamp=t0,
            value=Decimal("1000"),
            unit="W",
        )
        MeterReading.objects.create(
            charger=charger,
            transaction=tx,
            timestamp=t0 + timedelta(seconds=10),
            value=Decimal("1500"),
            unit="W",
        )
        key = store.identity_key(charger.charger_id, charger.connector_id)
        store.transactions[key] = tx
        resp = self.client.get(
            reverse(
                "charger-status-connector",
                args=[charger.charger_id, charger.connector_slug],
            )
        )
        self.assertEqual(resp.status_code, 200)
        chart = resp.context["chart_data"]
        self.assertEqual(len(chart["labels"]), 2)
        self.assertEqual(len(chart["datasets"]), 1)
        values = chart["datasets"][0]["values"]
        self.assertEqual(chart["datasets"][0]["connector_id"], 1)
        self.assertAlmostEqual(values[0], 1.0)
        self.assertAlmostEqual(values[1], 1.5)
        store.transactions.pop(key, None)

    def test_chart_data_uses_meter_start_for_register_values(self):
        charger = Charger.objects.create(charger_id="VIEWREG", connector_id=1)
        tx = Transaction.objects.create(
            charger=charger, start_time=timezone.now(), meter_start=746060
        )
        t0 = timezone.now()
        MeterReading.objects.create(
            charger=charger,
            transaction=tx,
            timestamp=t0,
            measurand="Energy.Active.Import.Register",
            value=Decimal("746.060"),
            unit="kWh",
        )
        MeterReading.objects.create(
            charger=charger,
            transaction=tx,
            timestamp=t0 + timedelta(seconds=10),
            measurand="Energy.Active.Import.Register",
            value=Decimal("746.080"),
            unit="kWh",
        )
        key = store.identity_key(charger.charger_id, charger.connector_id)
        store.transactions[key] = tx
        resp = self.client.get(
            reverse(
                "charger-status-connector",
                args=[charger.charger_id, charger.connector_slug],
            )
        )
        chart = resp.context["chart_data"]
        self.assertEqual(len(chart["labels"]), 2)
        self.assertEqual(len(chart["datasets"]), 1)
        values = chart["datasets"][0]["values"]
        self.assertEqual(chart["datasets"][0]["connector_id"], 1)
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[1], 0.02)
        self.assertAlmostEqual(resp.context["tx"].kw, 0.02)
        store.transactions.pop(key, None)

    def test_sessions_are_linked(self):
        charger = Charger.objects.create(charger_id="LINK1")
        tx = Transaction.objects.create(charger=charger, start_time=timezone.now())
        resp = self.client.get(reverse("charger-status", args=[charger.charger_id]))
        self.assertContains(resp, f"?session={tx.id}")

    def test_status_links_landing_page(self):
        charger = Charger.objects.create(charger_id="LAND1")
        resp = self.client.get(reverse("charger-status", args=[charger.charger_id]))
        self.assertContains(resp, reverse("charger-page", args=[charger.charger_id]))

    def test_configuration_link_hidden_for_non_staff(self):
        charger = Charger.objects.create(charger_id="CFG-HIDE")
        response = self.client.get(reverse("charger-status", args=[charger.charger_id]))
        admin_url = reverse("admin:ocpp_charger_change", args=[charger.pk])
        self.assertNotContains(response, admin_url)
        self.assertNotContains(response, _("Configuration"))

    def test_configuration_link_visible_for_staff(self):
        charger = Charger.objects.create(charger_id="CFG-SHOW")
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response = self.client.get(reverse("charger-status", args=[charger.charger_id]))
        admin_url = reverse("admin:ocpp_charger_change", args=[charger.pk])
        self.assertContains(response, admin_url)
        self.assertContains(response, _("Configuration"))

    def test_past_session_chart(self):
        charger = Charger.objects.create(charger_id="PAST1", connector_id=1)
        tx = Transaction.objects.create(
            charger=charger, start_time=timezone.now(), meter_start=0
        )
        t0 = timezone.now()
        MeterReading.objects.create(
            charger=charger,
            transaction=tx,
            timestamp=t0,
            value=Decimal("1000"),
            unit="W",
        )
        MeterReading.objects.create(
            charger=charger,
            transaction=tx,
            timestamp=t0 + timedelta(seconds=10),
            value=Decimal("1500"),
            unit="W",
        )
        resp = self.client.get(
            reverse(
                "charger-status-connector",
                args=[charger.charger_id, charger.connector_slug],
            )
            + f"?session={tx.id}"
        )
        self.assertContains(resp, "Back to live")
        chart = resp.context["chart_data"]
        self.assertEqual(len(chart["labels"]), 2)
        self.assertEqual(len(chart["datasets"]), 1)
        self.assertEqual(chart["datasets"][0]["connector_id"], 1)
        self.assertTrue(resp.context["past_session"])

    def test_aggregate_chart_includes_multiple_connectors(self):
        aggregate = Charger.objects.create(charger_id="VIEWAGG")
        connector_one = Charger.objects.create(
            charger_id="VIEWAGG", connector_id=1
        )
        connector_two = Charger.objects.create(
            charger_id="VIEWAGG", connector_id=2
        )
        base_time = timezone.now()
        tx_one = Transaction.objects.create(
            charger=connector_one, start_time=base_time, meter_start=0
        )
        tx_two = Transaction.objects.create(
            charger=connector_two, start_time=base_time, meter_start=0
        )
        MeterReading.objects.create(
            charger=connector_one,
            transaction=tx_one,
            timestamp=base_time,
            value=Decimal("1000"),
            unit="W",
        )
        MeterReading.objects.create(
            charger=connector_one,
            transaction=tx_one,
            timestamp=base_time + timedelta(seconds=15),
            value=Decimal("1500"),
            unit="W",
        )
        MeterReading.objects.create(
            charger=connector_two,
            transaction=tx_two,
            timestamp=base_time + timedelta(seconds=5),
            value=Decimal("2000"),
            unit="W",
        )
        MeterReading.objects.create(
            charger=connector_two,
            transaction=tx_two,
            timestamp=base_time + timedelta(seconds=20),
            value=Decimal("2600"),
            unit="W",
        )
        key_one = store.identity_key(
            connector_one.charger_id, connector_one.connector_id
        )
        key_two = store.identity_key(
            connector_two.charger_id, connector_two.connector_id
        )
        store.transactions[key_one] = tx_one
        store.transactions[key_two] = tx_two
        try:
            resp = self.client.get(
                reverse("charger-status", args=[aggregate.charger_id])
            )
            chart = resp.context["chart_data"]
            self.assertTrue(resp.context["show_chart"])
            self.assertEqual(len(chart["datasets"]), 2)
            data_map = {
                dataset["label"]: dataset["values"] for dataset in chart["datasets"]
            }
            connector_id_map = {
                dataset["label"]: dataset.get("connector_id")
                for dataset in chart["datasets"]
            }
            label_one = str(connector_one.connector_label)
            label_two = str(connector_two.connector_label)
            self.assertEqual(set(data_map), {label_one, label_two})
            self.assertEqual(len(data_map[label_one]), len(chart["labels"]))
            self.assertEqual(len(data_map[label_two]), len(chart["labels"]))
            self.assertTrue(any(value is not None for value in data_map[label_one]))
            self.assertTrue(any(value is not None for value in data_map[label_two]))
            self.assertEqual(connector_id_map[label_one], connector_one.connector_id)
            self.assertEqual(connector_id_map[label_two], connector_two.connector_id)
        finally:
            store.transactions.pop(key_one, None)
            store.transactions.pop(key_two, None)


class ChargerSessionPaginationTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="page", password="pwd")
        self.client.force_login(self.user)
        self.charger = Charger.objects.create(charger_id="PAGETEST")
        for i in range(15):
            Transaction.objects.create(
                charger=self.charger,
                start_time=timezone.now() - timedelta(minutes=i),
                meter_start=0,
            )

    def test_only_ten_transactions_shown(self):
        resp = self.client.get(
            reverse("charger-status", args=[self.charger.charger_id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["transactions"]), 10)
        self.assertTrue(resp.context["page_obj"].has_next())

    def test_session_search_by_date(self):
        date_str = timezone.now().date().isoformat()
        resp = self.client.get(
            reverse("charger-session-search", args=[self.charger.charger_id]),
            {"date": date_str},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["transactions"]), 15)


class LiveUpdateViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="lu", password="pw")
        self.client.force_login(self.user)

    def test_dashboard_includes_interval(self):
        resp = self.client.get(reverse("ocpp-dashboard"))
        self.assertEqual(resp.context["request"].live_update_interval, 5)
        self.assertContains(resp, "setInterval(() => location.reload()")

    def test_cp_simulator_includes_interval(self):
        resp = self.client.get(reverse("cp-simulator"))
        self.assertEqual(resp.context["request"].live_update_interval, 5)
        self.assertContains(resp, "setInterval(() => location.reload()")
