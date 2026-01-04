import pytest

from apps.video import utils


@pytest.mark.parametrize(
    "device_ok,binaries_ok,probe_ok,expected",
    [
        (True, True, True, True),
        (True, True, False, False),
    ],
)
def test_has_rpi_camera_stack_requires_probe(monkeypatch, device_ok, binaries_ok, probe_ok, expected):
    monkeypatch.setattr(utils, "_camera_device_accessible", lambda: device_ok)
    monkeypatch.setattr(utils, "_rpi_camera_binaries_ready", lambda: binaries_ok)
    monkeypatch.setattr(utils, "_probe_rpi_camera_stack", lambda: probe_ok)

    assert utils.has_rpi_camera_stack() is expected
