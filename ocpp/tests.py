
from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async
from django.test import Client, TransactionTestCase, TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from website.views import get_landing_apps
from django.utils import timezone

from config.asgi import application

from .models import Transaction, Charger, Simulator, MeterReading
from accounts.models import RFID, Account, Credit
from . import store
from django.db.models.deletion import ProtectedError


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
    async def test_transaction_saved(self):
        communicator = WebsocketCommunicator(application, "/TEST/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to([
            2,
            "1",
            "StartTransaction",
            {"meterStart": 10},
        ])
        response = await communicator.receive_json_from()
        tx_id = response[2]["transactionId"]

        tx = await database_sync_to_async(Transaction.objects.get)(
            transaction_id=tx_id, charger_id="TEST"
        )
        self.assertEqual(tx.meter_start, 10)
        self.assertIsNone(tx.stop_time)

        await communicator.send_json_to([
            2,
            "2",
            "StopTransaction",
            {"transactionId": tx_id, "meterStop": 20},
        ])
        await communicator.receive_json_from()

        await database_sync_to_async(tx.refresh_from_db)()
        self.assertEqual(tx.meter_stop, 20)
        self.assertIsNotNone(tx.stop_time)

        await communicator.disconnect()


class ChargerLandingTests(TestCase):
    def test_reference_created_and_page_renders(self):
        charger = Charger.objects.create(charger_id="PAGE1")
        self.assertIsNotNone(charger.reference)

        client = Client()
        response = client.get(reverse("charger-page", args=["PAGE1"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PAGE1")

    def test_status_page_renders(self):
        charger = Charger.objects.create(charger_id="PAGE2")
        client = Client()
        resp = client.get(reverse("charger-status", args=["PAGE2"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "PAGE2")

    def test_charger_page_shows_stats(self):
        charger = Charger.objects.create(charger_id="STATS")
        Transaction.objects.create(
            charger_id="STATS",
            transaction_id=1,
            meter_start=1000,
            meter_stop=3000,
            start_time=timezone.now(),
            stop_time=timezone.now(),
        )
        client = Client()
        resp = client.get(reverse("charger-page", args=["STATS"]))
        self.assertContains(resp, "2.00")
        self.assertContains(resp, "Offline")

    def test_log_page_renders_without_charger(self):
        store.logs["LOG1"] = ["hello"]
        client = Client()
        resp = client.get(reverse("charger-log", args=["LOG1"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "hello")
        store.logs.pop("LOG1", None)


class SimulatorLandingTests(TestCase):
    def test_simulator_page_in_nav(self):
        apps = get_landing_apps()
        ocpp_app = next((a for a in apps if a["name"] == "Ocpp"), None)
        self.assertIsNotNone(ocpp_app)
        paths = [v["path"] for v in ocpp_app["views"]]
        self.assertIn("/ocpp/simulator/", paths)


class ChargerAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin", password="secret", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_admin_lists_landing_link(self):
        charger = Charger.objects.create(charger_id="ADMIN1")
        url = reverse("admin:ocpp_charger_changelist")
        resp = self.client.get(url)
        self.assertContains(resp, charger.get_absolute_url())
        status_url = reverse("charger-status", args=["ADMIN1"])
        self.assertContains(resp, status_url)

    def test_admin_lists_log_link(self):
        charger = Charger.objects.create(charger_id="LOG1")
        url = reverse("admin:ocpp_charger_changelist")
        resp = self.client.get(url)
        log_url = reverse("charger-log", args=["LOG1"])
        self.assertContains(resp, log_url)

    def test_purge_action_removes_data(self):
        charger = Charger.objects.create(charger_id="PURGE1")
        Transaction.objects.create(
            charger_id="PURGE1",
            transaction_id=1,
            start_time=timezone.now(),
        )
        MeterReading.objects.create(
            charger=charger,
            timestamp=timezone.now(),
            value=1,
        )
        store.logs["PURGE1"] = ["entry"]
        url = reverse("admin:ocpp_charger_changelist")
        self.client.post(url, {"action": "purge_data", "_selected_action": [charger.pk]})
        self.assertFalse(Transaction.objects.filter(charger_id="PURGE1").exists())
        self.assertFalse(MeterReading.objects.filter(charger=charger).exists())
        self.assertNotIn("PURGE1", store.logs)

    def test_delete_requires_purge(self):
        charger = Charger.objects.create(charger_id="DEL1")
        Transaction.objects.create(
            charger_id="DEL1",
            transaction_id=1,
            start_time=timezone.now(),
        )
        delete_url = reverse("admin:ocpp_charger_delete", args=[charger.pk])
        with self.assertRaises(ProtectedError):
            self.client.post(delete_url, {"post": "yes"})
        self.assertTrue(Charger.objects.filter(pk=charger.pk).exists())
        url = reverse("admin:ocpp_charger_changelist")
        self.client.post(url, {"action": "purge_data", "_selected_action": [charger.pk]})
        self.client.post(delete_url, {"post": "yes"})
        self.assertFalse(Charger.objects.filter(pk=charger.pk).exists())


class SimulatorAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin2", password="secret", email="admin2@example.com"
        )
        self.client.force_login(self.admin)

    def test_admin_lists_log_link(self):
        sim = Simulator.objects.create(name="SIM", cp_path="SIMX")
        url = reverse("admin:ocpp_simulator_changelist")
        resp = self.client.get(url)
        log_url = reverse("charger-log", args=["SIMX"])
        self.assertContains(resp, log_url)

    def test_admin_shows_ws_url(self):
        sim = Simulator.objects.create(name="SIM2", cp_path="SIMY", host="h",
                                      ws_port=1111)
        url = reverse("admin:ocpp_simulator_changelist")
        resp = self.client.get(url)
        self.assertContains(resp, "ws://h:1111/SIMY/")

    def test_as_config_includes_custom_fields(self):
        sim = Simulator.objects.create(name="SIM3", cp_path="S3", interval=3.5,
                                      kwh_max=70)
        cfg = sim.as_config()
        self.assertEqual(cfg.interval, 3.5)
        self.assertEqual(cfg.kwh_max, 70)

    async def test_unknown_charger_auto_registered(self):
        communicator = WebsocketCommunicator(application, "/NEWCHG/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        exists = await database_sync_to_async(Charger.objects.filter(charger_id="NEWCHG").exists)()
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
        await database_sync_to_async(Charger.objects.create)(charger_id="RFID", require_rfid=True)
        communicator = WebsocketCommunicator(application, "/RFID/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to([
            2,
            "1",
            "StartTransaction",
            {"meterStart": 0},
        ])
        response = await communicator.receive_json_from()
        self.assertEqual(response[2]["idTagInfo"]["status"], "Invalid")

        exists = await database_sync_to_async(Transaction.objects.filter(charger_id="RFID").exists)()
        self.assertFalse(exists)

        await communicator.disconnect()

    async def test_rfid_required_accepts_known_tag(self):
        User = get_user_model()
        user = await database_sync_to_async(User.objects.create_user)(
            username="bob", password="pwd"
        )
        acc = await database_sync_to_async(Account.objects.create)(user=user)
        await database_sync_to_async(Credit.objects.create)(
            account=acc, amount_kwh=10
        )
        tag = await database_sync_to_async(RFID.objects.create)(rfid="CARDX")
        await database_sync_to_async(acc.rfids.add)(tag)
        await database_sync_to_async(Charger.objects.create)(charger_id="RFIDOK", require_rfid=True)
        communicator = WebsocketCommunicator(application, "/RFIDOK/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to([
            2,
            "1",
            "StartTransaction",
            {"meterStart": 5, "idTag": "CARDX"},
        ])
        response = await communicator.receive_json_from()
        self.assertEqual(response[2]["idTagInfo"]["status"], "Accepted")
        tx_id = response[2]["transactionId"]

        tx = await database_sync_to_async(Transaction.objects.get)(transaction_id=tx_id, charger_id="RFIDOK")
        self.assertEqual(tx.account_id, user.account.id)

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
        self.assertEqual(charger.last_meter_values.get("meterValue")[0]["sampledValue"][0]["value"], "42")

        await communicator.disconnect()


class ChargerLocationTests(TestCase):
    def test_lat_lon_fields_saved(self):
        charger = Charger.objects.create(
            charger_id="LOC1", latitude=10.123456, longitude=-20.654321
        )
        self.assertAlmostEqual(float(charger.latitude), 10.123456)
        self.assertAlmostEqual(float(charger.longitude), -20.654321)


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
                            "unit": "kWh",
                        }
                    ],
                }
            ],
        }
        await communicator.send_json_to([2, "1", "MeterValues", payload])
        await communicator.receive_json_from()

        reading = await database_sync_to_async(MeterReading.objects.get)(charger__charger_id="MR1")
        self.assertEqual(reading.transaction_id, 100)
        self.assertEqual(str(reading.value), "2.749")

        await communicator.disconnect()
