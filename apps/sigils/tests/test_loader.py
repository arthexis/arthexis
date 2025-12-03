import pytest
from django.apps import apps as django_apps
from django.db.models.signals import post_migrate

from apps.sigils import loader
from apps.sigils.models import SigilRoot


@pytest.mark.django_db
def test_load_fixture_sigil_roots_accepts_post_migrate_kwargs(monkeypatch):
    dummy_entries = iter([{"prefix": "test", "context_type": "dummy"}])
    monkeypatch.setattr(loader, "_iter_fixture_entries", lambda _path: dummy_entries)

    sender = django_apps.get_app_config("sigils")

    load_fixture_sigil_roots = loader.load_fixture_sigil_roots
    load_fixture_sigil_roots(
        signal=post_migrate,
        sender=sender,
        app_config=sender,
        verbosity=1,
        interactive=False,
        using="default",
        plan=None,
        apps=django_apps,
    )

    assert SigilRoot.objects.filter(prefix="test", context_type="dummy").exists()
