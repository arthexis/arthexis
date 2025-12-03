from importlib import import_module

from django.test import TestCase

from apps.protocols.models import Protocol
from apps.protocols.registry import clear_registry, get_registered_calls


class Ocpp16CoverageTests(TestCase):
    fixtures = ["protocols.json"]

    def setUp(self):
        clear_registry()
        # Import modules that host decorated call implementations.
        import_module("apps.ocpp.consumers")
        import_module("apps.ocpp.views")
        import_module("apps.ocpp.tasks")
        import_module("apps.ocpp.admin")

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
