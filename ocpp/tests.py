
from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async
from django.test import TransactionTestCase
from django.contrib.auth import get_user_model

from config.asgi import application
from .models import Transaction, Charger
from accounts.models import RFID


class CSMSConsumerTests(TransactionTestCase):
    async def test_transaction_saved(self):
        communicator = WebsocketCommunicator(application, "/ws/ocpp/TEST/")
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

    async def test_unknown_charger_auto_registered(self):
        communicator = WebsocketCommunicator(application, "/ws/ocpp/NEWCHG/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        exists = await database_sync_to_async(Charger.objects.filter(charger_id="NEWCHG").exists)()
        self.assertTrue(exists)

        await communicator.disconnect()

    async def test_rfid_required_rejects_invalid(self):
        await database_sync_to_async(Charger.objects.create)(charger_id="RFID", require_rfid=True)
        communicator = WebsocketCommunicator(application, "/ws/ocpp/RFID/")
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
        user = await database_sync_to_async(User.objects.create_user)(username="bob", password="pwd")
        await database_sync_to_async(RFID.objects.create)(uid="CARDX", user=user)
        await database_sync_to_async(Charger.objects.create)(charger_id="RFIDOK", require_rfid=True)
        communicator = WebsocketCommunicator(application, "/ws/ocpp/RFIDOK/")
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

        exists = await database_sync_to_async(Transaction.objects.filter(transaction_id=tx_id, charger_id="RFIDOK").exists)()
        self.assertTrue(exists)

    async def test_status_fields_updated(self):
        communicator = WebsocketCommunicator(application, "/ws/ocpp/STAT/")
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
