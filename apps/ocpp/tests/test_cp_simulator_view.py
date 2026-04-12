from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse


@pytest.mark.django_db
@patch("apps.ocpp.views.simulator._start_simulator")
@patch(
    "apps.ocpp.views.simulator.get_cpsim_request_metadata",
    return_value={"queued": False, "lock_path": "/tmp/.locks/cpsim-service.lck"},
)
@patch(
    "apps.ocpp.views.simulator.get_simulator_backend_choices",
    return_value=(("arthexis", "arthexis"), ("mobilityhouse", "mobilityhouse")),
)
def test_cp_simulator_start_posts_selected_backend(
    _request_metadata,
    _backend_choices,
    start_simulator,
    admin_client,
):
    response = admin_client.post(
        reverse("ocpp:cp-simulator"),
        {
            "action": "start",
            "host": "localhost:8888",
            "cp_path": "CP2",
            "serial_number": "CP2",
            "connector_id": "1",
            "rfid": "FFFFFFFF",
            "vin": "WP0ZZZ00000000000",
            "duration": "600",
            "interval": "5",
            "pre_charge_delay": "0",
            "average_kwh": "60",
            "amperage": "90",
            "repeat": "False",
            "simulator_backend": "arthexis",
        },
        follow=True,
    )

    assert response.status_code == 200
    start_simulator.assert_called_once()
    sim_params = start_simulator.call_args.args[0]
    assert sim_params["simulator_backend"] == "arthexis"
