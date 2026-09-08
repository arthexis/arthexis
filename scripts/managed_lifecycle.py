#!/usr/bin/env python3
from __future__ import annotations

import argparse

from apps.core.system.lifecycle import managed_layout, prepare_managed_install


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Arthexis managed lifecycle bridge")
    parser.add_argument("--root", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("layout")

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--editable", action="store_true")
    prepare.add_argument("--no-migrate", action="store_true")
    prepare.add_argument("--no-collectstatic", action="store_true")
    return parser


def main() -> int:
    options = _parser().parse_args()
    layout = managed_layout(options.root)

    if options.command == "layout":
        print(f"root={layout.root}")
        print(f"checkout={layout.checkout}")
        print(f"environment={layout.environment}")
        print(f"python={layout.python}")
        return 0

    prepare_managed_install(
        layout=layout,
        editable=options.editable,
        run_migrations=not options.no_migrate,
        run_collectstatic=not options.no_collectstatic,
    )
    print(layout.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
