from __future__ import annotations

import types
from pathlib import Path

import manage


class _FakeConfig:
    def __init__(self, *, pk: int, name: str, cert: str | None, key: str | None):
        self.pk = pk
        self.name = name
        self._cert = cert
        self._key = key

    def resolve_tls_paths(self):
        cert = Path(self._cert) if self._cert else None
        key = Path(self._key) if self._key else None
        return cert, key


class _FakeQuerySet:
    def __init__(self, items: list[_FakeConfig]):
        self._items = list(items)

    def filter(self, **kwargs):
        items = self._items
        if "name__iexact" in kwargs:
            target = str(kwargs["name__iexact"]).lower()
            items = [item for item in items if item.name.lower() == target]
        return _FakeQuerySet(items)

    def order_by(self, field: str):
        reverse = field.startswith("-")
        key = field.lstrip("-")
        return _FakeQuerySet(sorted(self._items, key=lambda item: getattr(item, key), reverse=reverse))

    def first(self):
        return self._items[0] if self._items else None

    def __iter__(self):
        return iter(self._items)


class _FakeSiteConfiguration:
    objects: _FakeQuerySet



def test_resolve_runserver_direct_tls_material_prefers_requested_host(monkeypatch):
    _FakeSiteConfiguration.objects = _FakeQuerySet(
        [
            _FakeConfig(pk=1, name="prod.example.com", cert="/tmp/prod.crt", key="/tmp/prod.key"),
            _FakeConfig(pk=2, name="localhost", cert="/tmp/local.crt", key="/tmp/local.key"),
        ]
    )
    fake_models = types.ModuleType("apps.nginx.models")
    fake_models.SiteConfiguration = _FakeSiteConfiguration
    monkeypatch.setitem(__import__("sys").modules, "apps.nginx.models", fake_models)

    cert, key = manage._resolve_runserver_direct_tls_material(preferred_names=["localhost"])

    assert cert == "/tmp/local.crt"
    assert key == "/tmp/local.key"



def test_resolve_runserver_direct_tls_material_falls_back_to_first_valid(monkeypatch):
    _FakeSiteConfiguration.objects = _FakeQuerySet(
        [
            _FakeConfig(pk=1, name="prod.example.com", cert=None, key=None),
            _FakeConfig(pk=2, name="staging.example.com", cert="/tmp/stage.crt", key="/tmp/stage.key"),
        ]
    )
    fake_models = types.ModuleType("apps.nginx.models")
    fake_models.SiteConfiguration = _FakeSiteConfiguration
    monkeypatch.setitem(__import__("sys").modules, "apps.nginx.models", fake_models)

    cert, key = manage._resolve_runserver_direct_tls_material(preferred_names=["localhost"])

    assert cert == "/tmp/stage.crt"
    assert key == "/tmp/stage.key"



def test_is_runserver_asgi_enabled_honors_noasgi_flag():
    assert manage._is_runserver_asgi_enabled({}) is True
    assert manage._is_runserver_asgi_enabled({"use_asgi": True}) is True
    assert manage._is_runserver_asgi_enabled({"use_asgi": False}) is False
