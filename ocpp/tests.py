from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async
from django.test import TransactionTestCase

from config.asgi import application
from .models import Transaction


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
