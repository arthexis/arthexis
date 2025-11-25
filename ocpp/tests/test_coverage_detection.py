from ocpp.management.commands.ocpp16_coverage import _implemented_csms_to_cp


def test_csms_to_cp_actions_include_views_admin_and_tasks(tmp_path):
    app_dir = tmp_path / "ocpp_app"
    app_dir.mkdir()

    (app_dir / "views.py").write_text(
        """
import json

def send_change_availability():
    msg = json.dumps([2, "a", "ChangeAvailability", {}])
    return msg
""",
        encoding="utf-8",
    )

    (app_dir / "admin.py").write_text(
        """
import json

def clear_profile():
    msg = json.dumps([2, "b", "ClearChargingProfile", {}])
    return msg
""",
        encoding="utf-8",
    )

    (app_dir / "tasks.py").write_text(
        """
import json

def request_composite_schedule():
    ocpp_action = "GetCompositeSchedule"
    msg = json.dumps([2, "c", ocpp_action, {}])
    return msg
""",
        encoding="utf-8",
    )

    actions = _implemented_csms_to_cp(app_dir)

    assert actions == {
        "ChangeAvailability",
        "ClearChargingProfile",
        "GetCompositeSchedule",
    }
