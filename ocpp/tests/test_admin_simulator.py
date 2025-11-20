from unittest.mock import MagicMock, patch

from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from ocpp import store
from ocpp.admin import SimulatorAdmin
from ocpp.models import Simulator


class SimulatorAdminDefaultActionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = AdminSite()
        User = get_user_model()
        self.user = User.objects.create_superuser("admin", "admin@example.com", "password")

    def _build_request(self, action: str):
        request = self.factory.post("/admin/ocpp/simulator/", {"action": action})
        request.user = self.user
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def test_start_default_simulator_action_runs_without_queryset(self):
        simulator = Simulator.objects.create(name="Default", cp_path="DEFAULT", default=True)
        admin = SimulatorAdmin(Simulator, self.admin_site)
        fake_simulator = MagicMock()
        fake_simulator.start.return_value = (True, "started", "log.log")

        with (
            patch("ocpp.admin.ChargePointSimulator", return_value=fake_simulator),
            patch("ocpp.admin.store.register_log_name"),
            patch.object(store, "simulators", {}),
        ):
            request = self._build_request("start_default_simulator")
            admin.response_action(request, Simulator.objects.none())

            self.assertIs(store.simulators.get(simulator.pk), fake_simulator)
            messages = [message.message for message in request._messages]
            self.assertTrue(any("Default" in message for message in messages))

