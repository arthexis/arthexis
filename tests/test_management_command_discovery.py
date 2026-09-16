from __future__ import annotations

from django.core.management import get_commands, load_command_class


def test_arthexis_management_commands_are_importable() -> None:
    failures: list[str] = []

    for name, app_name in sorted(get_commands().items()):
        if not isinstance(app_name, str) or not app_name.startswith("apps."):
            continue
        try:
            load_command_class(app_name, name)
        except Exception as exc:  # pragma: no cover - assertion reports the command
            failures.append(f"{app_name}.{name}: {type(exc).__name__}: {exc}")

    assert not failures, "\n".join(failures)
