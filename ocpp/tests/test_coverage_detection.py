import io
import json

from django.core.management import call_command

from ocpp.management.commands import ocpp16_coverage


def test_cp_to_csms_actions_include_receive_and_handler(tmp_path):
    app_dir = tmp_path / "ocpp_app"
    app_dir.mkdir()

    (app_dir / "consumers.py").write_text(
        """
class CSMSConsumer:
    async def receive(self, text_data=None):
        if text_data:
            action = "BootNotification"
        else:
            action = "Authorize"
        if action == "BootNotification":
            return action
        if "Authorize" == action:
            return action

    async def _handle_call_message(self, action, *args, **kwargs):
        action_handlers = {
            "StatusNotification": None,
            "MeterValues": None,
        }
        if action == "StatusNotification":
            return action
""",
        encoding="utf-8",
    )

    actions = ocpp16_coverage._implemented_cp_to_csms(app_dir)

    assert actions == {"Authorize", "BootNotification", "MeterValues", "StatusNotification"}


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

    actions = ocpp16_coverage._implemented_csms_to_cp(app_dir)

    assert actions == {
        "ChangeAvailability",
        "ClearChargingProfile",
        "GetCompositeSchedule",
    }


def test_ocpp16_coverage_command_outputs_summary_and_badge(tmp_path, monkeypatch):
    spec = {"cp_to_csms": ["Authorize", "BootNotification"], "csms_to_cp": ["Reset"]}
    monkeypatch.setattr(ocpp16_coverage, "_load_spec", lambda: spec)
    monkeypatch.setattr(
        ocpp16_coverage, "_implemented_cp_to_csms", lambda app_dir: {"Authorize"}
    )
    monkeypatch.setattr(
        ocpp16_coverage, "_implemented_csms_to_cp", lambda app_dir: {"Reset"}
    )

    json_path = tmp_path / "coverage.json"
    badge_path = tmp_path / "badge.svg"

    stdout = io.StringIO()
    stderr = io.StringIO()

    call_command(
        "ocpp16_coverage",
        "--json-path",
        str(json_path),
        "--badge-path",
        str(badge_path),
        stdout=stdout,
        stderr=stderr,
    )

    output = json.loads(stdout.getvalue())
    expected_summary = {
        "spec": spec,
        "implemented": {
            "cp_to_csms": ["Authorize"],
            "csms_to_cp": ["Reset"],
        },
        "coverage": {
            "cp_to_csms": {
                "supported": ["Authorize"],
                "count": 1,
                "total": 2,
                "percent": 50.0,
            },
            "csms_to_cp": {
                "supported": ["Reset"],
                "count": 1,
                "total": 1,
                "percent": 100.0,
            },
            "overall": {
                "supported": ["Authorize", "Reset"],
                "count": 2,
                "total": 3,
                "percent": 66.67,
            },
        },
    }

    assert output == expected_summary
    assert json.loads(json_path.read_text(encoding="utf-8")) == expected_summary

    badge_svg = badge_path.read_text(encoding="utf-8")
    assert "ocpp 1.6" in badge_svg
    assert "66.7%" in badge_svg

    stderr_output = stderr.getvalue()
    assert "coverage is incomplete" in stderr_output
    assert "Command completed without failure." in stderr_output
