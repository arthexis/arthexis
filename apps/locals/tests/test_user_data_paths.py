from pathlib import Path

from django.conf import settings

from apps.locals.user_data import fixtures as user_data


def test_user_data_root_uses_managed_data_dir(monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    # This file makes the historical <checkout>/data fallback unusable. If the
    # fixture code regresses to that path, mkdir() will fail immediately.
    (checkout / "data").write_text("checkout is not a mutable data directory")

    managed_data = tmp_path / "var" / "lib"
    monkeypatch.setattr(settings, "BASE_DIR", checkout)
    monkeypatch.setenv("ARTHEXIS_MODE", "installed")
    monkeypatch.setenv("ARTHEXIS_DATA_DIR", str(managed_data))

    assert user_data._data_root() == managed_data
    assert managed_data.is_dir()
    assert (checkout / "data").is_file()


def test_user_data_root_preserves_checkout_default(monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(settings, "BASE_DIR", checkout)
    monkeypatch.delenv("ARTHEXIS_MODE", raising=False)
    monkeypatch.delenv("ARTHEXIS_DATA_DIR", raising=False)

    assert user_data._data_root() == Path(checkout) / "data"
