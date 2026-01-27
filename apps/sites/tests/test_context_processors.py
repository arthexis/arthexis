import types

import pytest
from django.db.utils import OperationalError
from django.test import RequestFactory

from apps.modules.models import Module
from apps.nodes.models import NodeFeature
from apps.sites.models import Landing
from apps.sites import context_processors


@pytest.mark.django_db
def test_nav_links_handles_missing_modules_table(monkeypatch):
    request = RequestFactory().get("/admin/")

    monkeypatch.setattr(context_processors.Node, "get_local", staticmethod(lambda: None))

    class BrokenQuerySet:
        def filter(self, **kwargs):
            return self

        def select_related(self, *args, **kwargs):
            return self

        def prefetch_related(self, *args, **kwargs):
            return self

        def __iter__(self):
            raise OperationalError("modules table missing")

    monkeypatch.setattr(
        context_processors.Module.objects,
        "for_role",
        types.MethodType(lambda self, role: BrokenQuerySet(), context_processors.Module.objects),
    )

    context = context_processors.nav_links(request)

    assert context["nav_modules"] == []


@pytest.mark.django_db
def test_nav_links_hides_modules_with_disabled_features(monkeypatch):
    request = RequestFactory().get("/apps/")

    monkeypatch.setattr(context_processors.Node, "get_local", staticmethod(lambda: None))
    monkeypatch.setattr(NodeFeature, "is_enabled", property(lambda self: False))

    feature = NodeFeature.objects.create(slug="rfid-scanner", display="RFID Scanner")
    module = Module.objects.create(path="apps")
    module.features.add(feature)
    Landing.objects.create(module=module, path="/apps/", label="Apps")

    context = context_processors.nav_links(request)

    assert context["nav_modules"] == []


@pytest.mark.django_db
def test_nav_links_hides_landings_with_disabled_required_features(monkeypatch):
    request = RequestFactory().get("/ocpp/")

    monkeypatch.setattr(context_processors.Node, "get_local", staticmethod(lambda: None))
    monkeypatch.setattr(NodeFeature, "is_enabled", property(lambda self: False))

    NodeFeature.objects.create(slug="rfid-scanner", display="RFID Scanner")
    NodeFeature.objects.create(slug="rpi-camera", display="RPI Camera")
    module = Module.objects.create(path="ocpp")
    Landing.objects.create(
        module=module,
        path="/ocpp/rfid/validator/",
        label="RFID Card Validator",
    )

    context = context_processors.nav_links(request)

    assert context["nav_modules"] == []
