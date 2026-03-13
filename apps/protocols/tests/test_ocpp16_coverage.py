from importlib import import_module, reload
import json
from pathlib import Path

from django.test import TestCase

from apps.protocols.models import Protocol
from apps.protocols.registry import (
    clear_registry,
    get_registered_calls,
    rehydrate_from_module,
)
from apps.protocols.services import load_protocol_spec_from_file, spec_path


class Ocpp16SpecTests(TestCase):
    """Validate OCPP 1.6 spec call lists against the checked-in registry fixture."""

    def test_spec_matches_call_registry_fixture(self):
        """Assert OCPP 1.6 spec calls match registry fixture entries.

        Args:
            None

        Returns:
            None

        Raises:
            AssertionError: If spec calls diverge from registry fixture entries.
        """

        project_root = Path(__file__).resolve().parents[3]
        registry_path = project_root / "apps/ocpp/spec/ocpp16_calls.json"
        registry_calls = json.loads(registry_path.read_text(encoding="utf-8"))

        spec = load_protocol_spec_from_file(spec_path("ocpp16"))
        spec_calls = spec["calls"]

        for direction in ("cp_to_csms", "csms_to_cp"):
            with self.subTest(direction=direction):
                self.assertCountEqual(
                    spec_calls[direction],
                    registry_calls[direction],
                    msg=f"Spec mismatch for {direction}",
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
        """Regression: ensure OCPP 1.6 protocol calls stay registered."""
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
