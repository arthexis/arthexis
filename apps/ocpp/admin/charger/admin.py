"""Charger admin orchestration module."""

from ..common_imports import *

from .base import ChargerAdminBaseMixin
from .diagnostics import ChargerDiagnosticsMixin
from .metrics import ChargerMetricsMixin
from .remote_actions import ChargerRemoteActionsMixin
from .rfid import ChargerRfidMixin
from .simulator import ChargerSimulatorMixin


class ChargerAdmin(
    ChargerDiagnosticsMixin,
    ChargerRemoteActionsMixin,
    ChargerRfidMixin,
    ChargerSimulatorMixin,
    ChargerMetricsMixin,
    ChargerAdminBaseMixin,
):
    """Composed charger admin."""

    actions = [
        "purge_data",
        "fetch_cp_configuration",
        "toggle_rfid_authentication",
        "send_rfid_list_to_evcs",
        "update_rfids_from_evcs",
        "recheck_charger_status",
        "setup_cp_diagnostics",
        "request_cp_diagnostics",
        "get_diagnostics",
        "change_availability_operative",
        "change_availability_inoperative",
        "set_availability_state_operative",
        "set_availability_state_inoperative",
        "clear_authorization_cache",
        "clear_charging_profiles",
        "remote_stop_transaction",
        "reset_chargers",
        "create_simulator_for_cp",
        "view_charge_point_dashboard",
        "delete_selected",
    ]

    @admin.action(description=_("View in Site"))
    def view_charge_point_dashboard(self, request, queryset=None):
        return HttpResponseRedirect(reverse("ocpp:ocpp-dashboard"))

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "view-in-site/",
                self.admin_site.admin_view(self.view_charge_point_dashboard),
                name="ocpp_charger_view_charge_point_dashboard",
            ),
        ]
        return custom + urls
