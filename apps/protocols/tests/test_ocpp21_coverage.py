import json
from importlib import import_module, reload
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from apps.protocols.models import Protocol
from apps.protocols.registry import (
    clear_registry,
    get_registered_calls,
    rehydrate_from_module,
)
from apps.protocols.services import load_protocol_spec_from_file, spec_path


class Ocpp21SpecTests(SimpleTestCase):
    def test_spec_matches_call_registry_fixture(self):
        project_root = Path(__file__).resolve().parents[3]
        registry_path = project_root / "apps/ocpp/spec/ocpp21_calls.json"
        registry_calls = json.loads(registry_path.read_text(encoding="utf-8"))

        spec = load_protocol_spec_from_file(spec_path("ocpp21"))
        spec_calls = spec["calls"]

        for direction in ("cp_to_csms", "csms_to_cp"):
            with self.subTest(direction=direction):
                self.assertEqual(
                    set(spec_calls[direction]),
                    set(registry_calls[direction]),
                    msg=f"Spec mismatch for {direction}",
                )
                self.assertEqual(
                    len(spec_calls[direction]),
                    len(registry_calls[direction]),
                    msg=f"Spec count mismatch for {direction}",
                )


class Ocpp21CoverageTests(TestCase):
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

    def test_all_ocpp21_calls_have_registered_paths(self):
        protocol = Protocol.objects.get(slug="ocpp21")
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
