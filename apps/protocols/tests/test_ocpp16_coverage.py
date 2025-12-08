from importlib import import_module, reload

from django.test import TestCase

from apps.protocols.models import Protocol
from apps.protocols.registry import (
    clear_registry,
    get_registered_calls,
    rehydrate_from_module,
)


class Ocpp16CoverageTests(TestCase):
    fixtures = ["protocols.json"]

    def setUp(self):
        clear_registry()
        # Import (or reload) modules that host decorated call implementations so
        # decorators re-run after the registry reset.
        for module_path in (
            "apps.ocpp.consumers",
            "apps.ocpp.views",
            "apps.ocpp.views.actions",
            "apps.ocpp.tasks",
            "apps.ocpp.admin",
            "apps.ocpp.coverage_stubs",
        ):
            module = import_module(module_path)
            module = reload(module)
            rehydrate_from_module(module)

    def test_all_ocpp16_calls_have_registered_paths(self):
        protocol = Protocol.objects.get(slug="ocpp16")
        missing: list[str] = []
        for call in protocol.calls.order_by("direction", "name"):
            registered = get_registered_calls(protocol.slug, call.direction)
            callables = registered.get(call.name)
            if not callables:
                missing.append(f"{call.direction}:{call.name}")
        self.assertFalse(
            missing,
            msg="Missing protocol bindings: " + ", ".join(missing),
        )
