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


@pytest.mark.django_db
@patch(
    "apps.ocpp.views.simulator.get_cpsim_request_metadata",
    return_value={"queued": False, "lock_path": "/tmp/.locks/cpsim-service.lck"},
)
@patch(
    "apps.ocpp.views.simulator.get_simulator_backend_choices",
    return_value=(("arthexis", "arthexis"), ("mobilityhouse", "mobilityhouse")),
)
def test_cp_simulator_backend_selector_has_noscript_apply_fallback(
    _request_metadata, _backend_choices, admin_client
):
    response = admin_client.get(reverse("ocpp:cp-simulator"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert 'name="simulator_backend" value="mobilityhouse"' in content
    assert "<noscript>" in content
    assert '<button type="submit" class="btn btn-sm btn-outline-secondary">Apply</button>' in content


@pytest.mark.django_db
@patch(
    "apps.ocpp.views.simulator.get_cpsim_request_metadata",
    return_value={
        "queued": True,
        "lock_path": "/workspace/arthexis/.locks/cpsim-service.lck",
        "age_seconds": 42,
    },
)
@patch(
    "apps.ocpp.views.simulator.get_simulator_state",
    return_value={
        "running": True,
        "last_status": "cpsim-service start requested",
        "last_command": "start",
        "last_error": "",
        "last_message": "",
        "phase": "Service",
        "start_time": "2026-04-11 09:28:28",
        "stop_time": None,
        "params": {"host": "localhost", "ws_port": 8888, "cp_path": "CP2"},
    },
)
@patch(
    "apps.ocpp.views.simulator.get_simulator_backend_choices",
    return_value=(("arthexis", "arthexis"),),
)
def test_cp_simulator_shows_queued_service_warning_and_target_url(
    _backend_choices,
    _state,
    _request_metadata,
    admin_client,
):
    response = admin_client.get(reverse("ocpp:cp-simulator"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "ws://localhost:8888/CP2" in content
    assert "Start request queued for cpsim-service." in content
    assert "Queue age: 42s" in content
