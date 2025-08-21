from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class ScanNextViewTests(TestCase):
    @patch(
        "rfid.views.read_rfid",
        return_value={
            "rfid": "ABCD1234",
            "label_id": 1,
            "created": False,
            "block_data": "HELLO",
        },
    )
    def test_scan_next_success(self, mock_read):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "rfid": "ABCD1234",
                "label_id": 1,
                "created": False,
                "block_data": "HELLO",
            },
        )

    @patch("rfid.views.read_rfid", return_value={"error": "boom"})
    def test_scan_next_error(self, mock_read):
        resp = self.client.get(reverse("rfid-scan-next"))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "boom"})


class ReadRFIDTests(TestCase):
    def test_reads_and_decodes_block(self):
        import types

        class DummyMFRC522:
            MI_OK = 0
            PICC_REQIDL = 0x26
            PICC_AUTHENT1A = 0x60
            PICC_AUTHENT1B = 0x61
            instance = None

            def __init__(self):
                self.__class__.instance = self
                self.auth_params = []

            def MFRC522_Request(self, _mode):
                return (self.MI_OK, None)

            def MFRC522_Anticoll(self):
                return (self.MI_OK, [0xAB, 0xCD, 0x12, 0x34])

            def MFRC522_SelectTag(self, _uid):
                pass

            def MFRC522_Auth(self, mode, block, key, uid):
                self.auth_params.append((mode, block, key, uid))
                return self.MI_OK

            def MFRC522_Read(self, _block):
                return [ord(c) for c in "HELLO"] + [0] * 11

            def MFRC522_StopCrypto1(self):
                pass

        module = types.SimpleNamespace(MFRC522=DummyMFRC522)
        with patch.dict("sys.modules", {"mfrc522": module}):
            from rfid import views
            from accounts.models import RFID

            tag = RFID.objects.create(rfid="ABCD1234")
            result = views.read_rfid()

        self.assertEqual(result["block_data"], "HELLO")
        tag.refresh_from_db()
        self.assertEqual(tag.block_data, b"HELLO" + b"\x00" * 11)
        dummy = DummyMFRC522.instance
        self.assertIn(
            (dummy.PICC_AUTHENT1A, 8, [255] * 6, [0xAB, 0xCD, 0x12, 0x34]),
            dummy.auth_params,
        )
