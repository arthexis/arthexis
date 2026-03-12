import pytest
from django.core.management.base import CommandError

from apps.playwright.management.commands.preview import Command


def test_handle_reports_engine_failures_without_name_error(monkeypatch) -> None:
    """Engine failure aggregation should raise a clean CommandError message."""

    command = Command()

    deleted_ids: list[int | None] = []

    monkeypatch.setattr(command, "_create_throwaway_admin_user", lambda: ("tmp", "pw", 42))
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", deleted_ids.append)
    monkeypatch.setattr(command, "_build_capture_plan", lambda **kwargs: [])

    def _always_fail(**kwargs):
        raise CommandError("boom")

    monkeypatch.setattr(command, "_capture_all", _always_fail)

    with pytest.raises(CommandError, match=r"All preview engines failed\. Last error: boom"):
        command.handle(
            base_url="http://127.0.0.1:8000",
            paths=["/admin/"],
            username=None,
            password=None,
            output="media/previews/admin-preview.png",
            output_dir="",
            viewports="desktop",
            engine="chromium,firefox",
            no_login=False,
        )

    assert deleted_ids == [42]


def test_handle_uses_throwaway_user_and_cleans_it_up(monkeypatch) -> None:
    """Preview captures should use a temporary login user and remove it afterwards."""

    command = Command()
    state: dict[str, object] = {"created": False, "deleted": None, "login_required": None}

    def _create_user() -> tuple[str, str, int]:
        state["created"] = True
        return "preview-user", "preview-pass", 99

    def _delete_user(user_id: int | None) -> None:
        state["deleted"] = user_id

    def _capture_all(**kwargs):
        state["login_required"] = kwargs["login_required"]

    monkeypatch.setattr(command, "_create_throwaway_admin_user", _create_user)
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", _delete_user)
    monkeypatch.setattr(command, "_build_capture_plan", lambda **kwargs: [])
    monkeypatch.setattr(command, "_capture_all", _capture_all)
    monkeypatch.setattr(command, "_print_reports", lambda captures: None)

    command.handle(
        base_url="http://127.0.0.1:8000",
        paths=["/admin/"],
        username=None,
        password=None,
        output="media/previews/admin-preview.png",
        output_dir="",
        viewports="desktop",
        engine="chromium",
        no_login=False,
    )

    assert state["created"] is True
    assert state["login_required"] is True
    assert state["deleted"] == 99


def test_handle_cleans_up_throwaway_user_on_validation_failure(monkeypatch) -> None:
    """Throwaway preview user should be deleted when argument validation fails."""

    command = Command()
    state: dict[str, object] = {"deleted": None}

    monkeypatch.setattr(command, "_create_throwaway_admin_user", lambda: ("preview-user", "preview-pass", 99))
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", lambda user_id: state.__setitem__("deleted", user_id))

    with pytest.raises(CommandError, match="At least one viewport profile"):
        command.handle(
            base_url="http://127.0.0.1:8000",
            paths=["/admin/"],
            username=None,
            password=None,
            output="media/previews/admin-preview.png",
            output_dir="",
            viewports=",",
            engine="chromium",
            no_login=False,
        )

    assert state["deleted"] == 99


def test_handle_skips_login_and_user_creation_for_no_login(monkeypatch) -> None:
    """No-login captures should not create or authenticate any temporary user."""

    command = Command()
    state: dict[str, object] = {"created": False, "deleted": None, "login_required": None}

    def _create_user() -> tuple[str, str, int]:
        state["created"] = True
        return "preview-user", "preview-pass", 99

    def _delete_user(user_id: int | None) -> None:
        state["deleted"] = user_id

    def _capture_all(**kwargs):
        state["login_required"] = kwargs["login_required"]

    monkeypatch.setattr(command, "_create_throwaway_admin_user", _create_user)
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", _delete_user)
    monkeypatch.setattr(command, "_build_capture_plan", lambda **kwargs: [])
    monkeypatch.setattr(command, "_capture_all", _capture_all)
    monkeypatch.setattr(command, "_print_reports", lambda captures: None)

    command.handle(
        base_url="http://127.0.0.1:8000",
        paths=["/admin/"],
        username=None,
        password=None,
        output="media/previews/admin-preview.png",
        output_dir="",
        viewports="desktop",
        engine="chromium",
        no_login=True,
    )

    assert state["created"] is False
    assert state["login_required"] is False
    assert state["deleted"] is None

