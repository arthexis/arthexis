from django.utils import timezone
from core.models import User, OdooProfile
from defusedxml import xmlrpc as defused_xmlrpc

defused_xmlrpc.monkey_patch()
xmlrpc_client = defused_xmlrpc.xmlrpc_client


class FakeCommon:
    def __init__(self, uid):
        self.uid = uid

    def authenticate(self, db, username, password, _):
        return self.uid


class FakeModels:
    def __init__(self, info=None, raise_error=False):
        self.info = info or {"name": "Odoo User", "email": "user@example.com"}
        self.raise_error = raise_error

    def execute_kw(self, db, uid, password, model, method, args, kwargs):
        if self.raise_error:
            raise Exception("fail")
        return [self.info]


def test_verify_success(monkeypatch):
    def fake_proxy(url):
        if url.endswith("/common"):
            return FakeCommon(uid=42)
        return FakeModels()

    monkeypatch.setattr(xmlrpc_client, "ServerProxy", fake_proxy)
    user = User.objects.create(username="u0")
    profile = OdooProfile.objects.create(
        user=user,
        host="http://test",
        database="db",
        username="u0",
        password="p",
    )
    assert profile.verify() is True
    profile.refresh_from_db()
    assert profile.odoo_uid == 42
    assert profile.name == "u0"
    assert profile.email == "user@example.com"
    assert profile.verified_on is not None


def test_credentials_change_resets_verification(monkeypatch):
    user = User.objects.create(username="u1")
    profile = OdooProfile.objects.create(
        user=user,
        host="http://test",
        database="db",
        username="u1",
        password="p",
    )
    profile.verified_on = timezone.now()
    profile.save()
    profile.password = "new"
    profile.save()
    profile.refresh_from_db()
    assert profile.verified_on is None


def test_execute_failure_marks_unverified(monkeypatch):
    user = User.objects.create(username="u2")
    profile = OdooProfile.objects.create(
        user=user,
        host="http://test",
        database="db",
        username="u2",
        password="p",
    )
    profile.odoo_uid = 42
    profile.verified_on = timezone.now()
    profile.save()

    def fake_proxy(url):
        return FakeModels(raise_error=True)

    monkeypatch.setattr(xmlrpc_client, "ServerProxy", fake_proxy)
    try:
        profile.execute("res.users", "read", [42])
    except Exception:
        pass
    profile.refresh_from_db()
    assert profile.verified_on is None


def test_execute_passes_kwargs(monkeypatch):
    user = User.objects.create(username="u5")
    profile = OdooProfile.objects.create(
        user=user,
        host="http://test",
        database="db",
        username="u5",
        password="p",
    )
    profile.odoo_uid = 99
    profile.verified_on = timezone.now()
    profile.save()

    captured: dict[str, object] = {}

    class CaptureModels:
        def execute_kw(self, db, uid, password, model, method, args, kwargs):
            captured.update(
                {
                    "db": db,
                    "uid": uid,
                    "password": password,
                    "model": model,
                    "method": method,
                    "args": args,
                    "kwargs": kwargs,
                }
            )
            return []

    def fake_proxy(url):
        return CaptureModels()

    monkeypatch.setattr(xmlrpc_client, "ServerProxy", fake_proxy)

    result = profile.execute(
        "product.product",
        "search_read",
        [("name", "ilike", "Test")],
        fields=["name"],
        limit=5,
    )

    assert result == []
    assert captured["args"] == [[("name", "ilike", "Test")]]
    assert captured["kwargs"] == {"fields": ["name"], "limit": 5}


def test_profile_string_resolves_sigil_values(monkeypatch):
    user = User.objects.create(username="u3")

    def fake_resolve(self, field):
        return {"username": "resolved-user", "database": "resolved-db"}.get(field, "")

    monkeypatch.setattr(OdooProfile, "resolve_sigils", fake_resolve)
    profile = OdooProfile.objects.create(
        user=user,
        host="http://test",
        database="[ODOO.DATABASE]",
        username="[ODOO.USERNAME]",
        password="secret",
    )
    profile.refresh_from_db()
    assert profile.name == "resolved-user"
    assert str(profile) == "resolved-user@resolved-db"
