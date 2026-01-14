from __future__ import annotations

from apps.audio.models import RecordingDevice


def test_preferred_device_prefers_usb(tmp_path):
    pcm_path = tmp_path / "pcm"
    pcm_path.write_text(
        """
        00-00: Built-in mic : built-in : playback 1 : capture 1
        01-00: USB Audio : USB Audio : playback 1 : capture 2
        """.strip()
    )

    preferred = RecordingDevice.preferred_device(pcm_path=pcm_path)

    assert preferred is not None
    assert preferred.identifier == "01-00"
    assert "USB" in preferred.raw_info


def test_identifier_to_alsa_device_handles_card_device_pairs():
    assert RecordingDevice.identifier_to_alsa_device("00-00") == "plughw:0,0"
    assert RecordingDevice.identifier_to_alsa_device("2-7") == "plughw:2,7"
    assert RecordingDevice.identifier_to_alsa_device("usb-mic") is None
