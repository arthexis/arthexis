from django.test import TransactionTestCase
from channels.testing import WebsocketCommunicator
from config.asgi import application


class RFIDConsumerTests(TransactionTestCase):
    async def test_websocket_connects(self):
        communicator = WebsocketCommunicator(application, "/ws/rfid/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()
