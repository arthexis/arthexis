import asyncio
import json
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase
from channels.testing import WebsocketCommunicator
from config.asgi import application


class RFIDConsumerTests(TransactionTestCase):
    async def test_websocket_connects(self):
        communicator = WebsocketCommunicator(application, "/ws/rfid/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_start_returns_status(self):
        with patch("rfid.consumers.read_rfid", return_value={"rfid": None}):
            communicator = WebsocketCommunicator(application, "/ws/rfid/")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_json_to({"action": "start"})
            resp = await communicator.receive_json_from()
            self.assertEqual(resp, {"status": "started"})
            await communicator.disconnect()

    async def test_start_handles_error(self):
        with patch("rfid.consumers.read_rfid", return_value={"error": "boom"}):
            communicator = WebsocketCommunicator(application, "/ws/rfid/")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_json_to({"action": "start"})
            resp = await communicator.receive_json_from()
            self.assertEqual(resp, {"error": "boom"})
            await communicator.disconnect()

    async def test_start_timeout(self):
        with patch(
            "rfid.consumers.asyncio.wait_for",
            new=AsyncMock(side_effect=asyncio.TimeoutError),
        ):
            communicator = WebsocketCommunicator(application, "/ws/rfid/")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_json_to({"action": "start"})
            resp = await communicator.receive_json_from()
            self.assertEqual(resp, {"error": "RFID reader timeout"})
            await communicator.disconnect()
