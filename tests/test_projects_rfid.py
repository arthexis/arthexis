import io
from unittest.mock import MagicMock

import pytest

from projects import rfid


pytestmark = [
    pytest.mark.role("Terminal"),
    pytest.mark.role("Control"),
    pytest.mark.role("Satellite"),
    pytest.mark.role("Constellation"),
]


class TestPinout:
    def test_returns_copy(self):
        mapping = rfid.pinout()
        mapping["SDA"] = "changed"
        assert mapping["SDA"] != rfid.PINOUT["SDA"]

    def test_expected_wiring(self):
        mapping = rfid.pinout()
        assert list(mapping.items()) == list(rfid.PINOUT.items())


class TestScan:
    def _setup_dependencies(self, monkeypatch, reader_factory=None, gpio=None):
        if reader_factory is None:
            reader_factory = MagicMock()
        if gpio is None:
            gpio = MagicMock()

        def fake_loader(stdout):
            return reader_factory, gpio

        monkeypatch.setattr(rfid, "_load_dependencies", fake_loader)
        monkeypatch.setattr(rfid, "_iter_spi_devices", lambda: [])
        return reader_factory, gpio

    def test_missing_dependencies(self, monkeypatch):
        buffer = io.StringIO()

        def failing_loader(stdout):
            rfid._print("install mfrc522", stdout)
            return None, None

        monkeypatch.setattr(rfid, "_load_dependencies", failing_loader)
        exit_code = rfid.scan(stdout=buffer)
        assert exit_code == 1
        assert "mfrc522" in buffer.getvalue().lower()

    def test_missing_spi_device_with_candidates(self, monkeypatch, tmp_path):
        buffer = io.StringIO()
        reader_factory, gpio = self._setup_dependencies(monkeypatch)
        missing = tmp_path / "spidev0.0"
        candidate = tmp_path / "spidev1.0"
        candidate.touch()
        monkeypatch.setattr(rfid, "_iter_spi_devices", lambda: [candidate])

        exit_code = rfid.scan(spi_device=missing, stdout=buffer)
        assert exit_code == 1
        output = buffer.getvalue()
        assert str(missing) in output
        assert str(candidate) in output
        assert not reader_factory.called
        assert not gpio.cleanup.called

    def test_permission_error(self, monkeypatch, tmp_path):
        buffer = io.StringIO()

        def factory():
            raise PermissionError("denied")

        reader_factory, gpio = self._setup_dependencies(
            monkeypatch, reader_factory=factory
        )
        device = tmp_path / "spidev0.0"
        device.touch()

        exit_code = rfid.scan(spi_device=device, stdout=buffer)
        assert exit_code == 1
        assert "permission" in buffer.getvalue().lower()
        assert not gpio.cleanup.called

    def test_successful_scan(self, monkeypatch, tmp_path):
        buffer = io.StringIO()
        stdin = io.StringIO("\n")

        class FakeReader:
            def __init__(self):
                self.calls = 0

            def read_no_block(self):
                self.calls += 1
                if self.calls == 1:
                    return (None, None)
                if self.calls == 2:
                    return (12345, "Hello\nWorld")
                return (None, None)

        factory = MagicMock(side_effect=FakeReader)
        reader_factory, gpio = self._setup_dependencies(
            monkeypatch, reader_factory=factory
        )
        device = tmp_path / "spidev0.0"
        device.touch()

        responses = iter([([], [], []), ([], [], []), ([stdin], [], [])])

        def fake_select(_rlist, _wlist, _xlist, _timeout):
            return next(responses)

        exit_code = rfid.scan(
            spi_device=device,
            stdout=buffer,
            stdin=stdin,
            select_fn=fake_select,
            sleep=lambda _t: None,
        )

        assert exit_code == 0
        output = buffer.getvalue()
        assert "Scanning for RFID cards" in output
        assert "12345" in output
        assert "Hello World" in output
        assert reader_factory.called
        gpio.cleanup.assert_called_once()

    def test_keyboard_interrupt(self, monkeypatch, tmp_path):
        buffer = io.StringIO()

        class FakeReader:
            def read_no_block(self):
                raise KeyboardInterrupt

        reader_factory, gpio = self._setup_dependencies(
            monkeypatch, reader_factory=lambda: FakeReader()
        )
        device = tmp_path / "spidev0.0"
        device.touch()

        exit_code = rfid.scan(
            spi_device=device,
            stdout=buffer,
            sleep=lambda _t: None,
        )

        assert exit_code == 0
        assert "interrupted" in buffer.getvalue().lower()
        gpio.cleanup.assert_called_once()
