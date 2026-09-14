from __future__ import annotations

import argparse

from apps.core.system import lifecycle
from apps.core.system.adoption import inspect_adoption


class AdoptionArgumentError(ValueError):
    """Raised when managed adoption flags do not describe a safe preflight."""


def _adoption_arguments(arguments: tuple[str, ...]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    parser.add_argument("--adopt", action="store_true")
    parser.add_argument("--from", dest="source")
    parser.add_argument("--dry-run", action="store_true")
    try:
        namespace, unknown = parser.parse_known_args(arguments)
    except argparse.ArgumentError as exc:
        raise AdoptionArgumentError(str(exc)) from exc
    if unknown:
        raise AdoptionArgumentError(
            f"unsupported adoption preflight arguments: {' '.join(unknown)}"
        )
    return namespace


def install(
    *arguments: str,
    layout: lifecycle.InstallationLayout | None = None,
) -> lifecycle.InstallationLayout | dict[str, object]:
    """Run a normal managed install or a read-only adoption preflight."""
    if "--adopt" not in arguments:
        return lifecycle.install(*arguments, layout=layout)

    options = _adoption_arguments(arguments)
    if not options.source:
        raise AdoptionArgumentError("--adopt requires --from PATH")
    if not options.dry_run:
        raise AdoptionArgumentError(
            "adoption execution is not implemented yet; rerun with --dry-run"
        )

    target = lifecycle._resolve_layout(layout)
    return inspect_adoption(options.source, target_root=target.root)
