import asyncio
import contextlib
import json

from channels.generic.websocket import AsyncWebsocketConsumer


def read_rfid() -> dict:
    """Read a single RFID tag using the MFRC522 reader."""
    try:
        from mfrc522 import MFRC522  # type: ignore
    except Exception as exc:  # pragma: no cover - hardware dependent
        return {"error": str(exc)}

    import time
    try:
        import RPi.GPIO as GPIO  # pragma: no cover - hardware dependent
    except Exception:  # pragma: no cover - hardware dependent
        GPIO = None

    mfrc = MFRC522()
    timeout = time.time() + 1
    try:
        while time.time() < timeout:  # pragma: no cover - hardware loop
            (status, _tag_type) = mfrc.MFRC522_Request(mfrc.PICC_REQIDL)
            if status == mfrc.MI_OK:
                (status, uid) = mfrc.MFRC522_Anticoll()
                if status == mfrc.MI_OK:
                    rfid = "".join(f"{x:02X}" for x in uid[:5])
                    return {"rfid": rfid}
            time.sleep(0.2)
        return {"rfid": None}
    finally:  # pragma: no cover - cleanup hardware
        if GPIO:
            try:
                GPIO.cleanup()
            except Exception:
                pass


class RFIDConsumer(AsyncWebsocketConsumer):
    """Stream RFID scans over a websocket."""

    async def connect(self):  # pragma: no cover - trivial
        await self.accept()
        self.scanning = False
        self.scan_task = None

    async def disconnect(self, code):  # pragma: no cover - trivial
        self.scanning = False
        if self.scan_task:
            self.scan_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.scan_task

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            data = {}
        if data.get("action") == "start" and not self.scanning:
            self.scanning = True
            self.scan_task = asyncio.create_task(self._scan_loop())

    async def _scan_loop(self):
        while self.scanning:
            result = await asyncio.to_thread(read_rfid)
            if result.get("rfid"):
                await self.send(json.dumps({"rfid": result["rfid"]}))
            elif result.get("error"):
                await self.send(json.dumps({"error": result["error"]}))
                self.scanning = False
            await asyncio.sleep(0.1)
