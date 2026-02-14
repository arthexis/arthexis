from __future__ import annotations

import json

from config.settings_helpers import load_stored_ip_addresses


def test_load_stored_ip_addresses_from_json_list(tmp_path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    lock_file = lock_dir / "local_ips.lck"
    lock_file.write_text(json.dumps(["192.168.1.10", "bad", "2001:db8::1"]), encoding="utf-8")

    addresses = load_stored_ip_addresses(tmp_path)

    assert addresses == {"192.168.1.10", "2001:db8::1"}


def test_load_stored_ip_addresses_from_lines(tmp_path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    lock_file = lock_dir / "local_ips.lck"
    lock_file.write_text("10.0.0.5\nnot-an-ip\n", encoding="utf-8")

    addresses = load_stored_ip_addresses(tmp_path)

    assert addresses == {"10.0.0.5"}
