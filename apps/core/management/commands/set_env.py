from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from dotenv import dotenv_values
from filelock import FileLock, Timeout


_VALID_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env_path() -> Path:
    return Path(settings.BASE_DIR) / "arthexis.env"


def _read_env(path: Path) -> OrderedDict[str, str]:
    if not path.exists():
        return OrderedDict()
    values = dotenv_values(path)
    return OrderedDict(
        (key, _unescape_env_value(value))
        for key, value in values.items()
        if key is not None and value is not None
    )


def _format_env_value(value: str) -> str:
    if value == "":
        return '""'
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
        .replace("!", "\\!")
    )
    return f'"{escaped}"'


def _unescape_env_value(value: str) -> str:
    return value.replace("\\$", "$").replace("\\`", "`").replace("\\!", "!")


def _validate_key(key: str) -> None:
    if not _VALID_KEY_RE.match(key):
        raise CommandError(
            f"Invalid key '{key}'. Use only letters, digits, and underscores, "
            "and start with a letter or underscore."
        )


def _write_env(path: Path, values: OrderedDict[str, str]) -> None:
    lines = [f"{key}={_format_env_value(value)}" for key, value in values.items()]
    if lines:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        if path.exists():
            path.unlink()


class Command(BaseCommand):
    help = (
        "Manage key/value pairs in arthexis.env so they are loaded by startup scripts. "
        "Use --set to define values, --get to inspect, --list to show all, and --delete "
        "to remove keys."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--set",
            nargs=2,
            action="append",
            metavar=("KEY", "VALUE"),
            help="Set a key/value pair (repeatable).",
        )
        parser.add_argument(
            "--get",
            action="append",
            metavar="KEY",
            help="Show the current value for a key (repeatable).",
        )
        parser.add_argument(
            "--delete",
            action="append",
            metavar="KEY",
            help="Delete a key from arthexis.env (repeatable).",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all stored key/value pairs.",
        )

    def handle(self, *args, **options):
        set_pairs = options.get("set") or []
        get_keys = options.get("get") or []
        delete_keys = options.get("delete") or []
        list_values = options.get("list")

        if not any([set_pairs, get_keys, delete_keys, list_values]):
            raise CommandError("Provide at least one action: --set, --get, --delete, --list.")

        env_path = _env_path()

        for key, _value in set_pairs:
            _validate_key(key)
        for key in delete_keys:
            _validate_key(key)
        for key in get_keys:
            _validate_key(key)

        values: OrderedDict[str, str]
        if set_pairs or delete_keys:
            env_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = env_path.with_suffix(env_path.suffix + ".lock")
            try:
                with FileLock(lock_path, timeout=5):
                    values = _read_env(env_path)
                    for key, value in set_pairs:
                        values[key] = value

                    for key in delete_keys:
                        if key in values:
                            values.pop(key)
                        else:
                            self.stdout.write(self.style.WARNING(f"Key not found: {key}"))

                    _write_env(env_path, values)
            except Timeout as exc:
                raise CommandError("Could not acquire lock to modify arthexis.env.") from exc
        else:
            values = _read_env(env_path)

        if list_values:
            if not values:
                self.stdout.write("No values stored in arthexis.env.")
            else:
                for key, value in values.items():
                    self.stdout.write(f"{key}={value}")

        for key in get_keys:
            if key not in values:
                raise CommandError(f"Key not found: {key}")
            self.stdout.write(f"{key}={values[key]}")
