import pytest
from django.conf import settings
from django.test import override_settings

from config.database_router import ModelDatabaseRouter

pytestmark = pytest.mark.filterwarnings(
    "ignore:Overriding setting DATABASES can lead to unexpected behavior.",
)


class _FakeMeta:
    def __init__(self, label_lower: str) -> None:
        self.label_lower = label_lower


def _fake_model(label_lower: str) -> type:
    return type("FakeModel", (), {"_meta": _FakeMeta(label_lower)})


def _persistent_override() -> override_settings:
    databases = settings.DATABASES.copy()
    databases["persistent"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
    mapping = dict(settings.MODEL_DATABASES)
    mapping["core.rfid"] = "persistent"
    return override_settings(DATABASES=databases, MODEL_DATABASES=mapping)


def test_router_uses_configured_alias():
    with _persistent_override():
        router = ModelDatabaseRouter()
        rfid_model = _fake_model("core.rfid")
        assert router.db_for_read(rfid_model) == "persistent"
        assert router.db_for_write(rfid_model) == "persistent"
        assert router.allow_migrate("persistent", "core", "rfid") is True
        assert router.allow_migrate("default", "core", "rfid") is False


def test_router_defaults_when_alias_missing():
    with override_settings(MODEL_DATABASES={"core.rfid": "persistent"}):
        router = ModelDatabaseRouter()
        rfid_model = _fake_model("core.rfid")
        assert router.db_for_read(rfid_model) == "default"
        assert router.db_for_write(rfid_model) == "default"
        assert router.allow_migrate("default", "core", "rfid") is True
        assert router.allow_migrate("persistent", "core", "rfid") is False


def test_router_blocks_cross_database_relations():
    with _persistent_override():
        router = ModelDatabaseRouter()
        rfid_model_class = _fake_model("core.rfid")
        default_model_class = _fake_model("core.user")
        rfid_obj = rfid_model_class()
        other_obj = default_model_class()
        assert router.allow_relation(rfid_obj, rfid_obj) is True
        assert router.allow_relation(rfid_obj, other_obj) is False
