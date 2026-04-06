from __future__ import annotations

import json

import pytest

from scripts.startup_orchestration import extract_payload


def test_extract_payload_raises_when_no_json_object_present() -> None:
    with pytest.raises(json.JSONDecodeError):
        extract_payload("Version: v0.2.3 r92795c\nstartup failed\n")
