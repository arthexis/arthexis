from __future__ import annotations

from dataclasses import dataclass

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


@dataclass(frozen=True)
class DoctorTaskDefinition:
    """Describe a doctor task that can be invoked from the unified command."""

    target: str
    group: str
    description: str
    command: tuple[str, ...]


DOCTOR_TASKS: dict[str, DoctorTaskDefinition] = {
    "core.good": DoctorTaskDefinition(
        target="core.good",
        group="core",
        description="Summarize suite readiness and highlight remediation priorities.",
        command=("good", "--details"),
    ),
    "core.health": DoctorTaskDefinition(
        target="core.health",
        group="core",
        description="Run non-interactive core health checks.",
        command=("health", "--group", "core"),
    ),
    "core.migrations": DoctorTaskDefinition(
        target="core.migrations",
        group="core",
        description="Verify migration files are in sync with models.",
        command=("migrations", "check"),
    ),
    "cards.rfid": DoctorTaskDefinition(
        target="cards.rfid",
        group="peripherals",
        description="Run RFID diagnostics through the cards app doctor.",
        command=("rfid", "doctor"),
    ),
    "video.camera": DoctorTaskDefinition(
        target="video.camera",
        group="peripherals",
        description="Run video diagnostics through the video app doctor.",
        command=("video", "doctor"),
    ),
}


class Command(BaseCommand):
    """Run suite diagnostics and dispatch dedicated app doctor commands."""

    help = "Run Arthexis diagnostics and optionally invoke dedicated app doctor commands."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--target",
            action="append",
            default=[],
            help="Specific doctor target in <app>.<task> form (can be repeated).",
        )
        parser.add_argument(
            "--group",
            action="append",
            default=[],
            help="Run all doctor targets in a group (e.g. --group core).",
        )
        parser.add_argument("--all", action="store_true", help="Run every doctor target across groups.")
        parser.add_argument("--list-targets", action="store_true", help="List available doctor targets and exit.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Propagate force-repair mode to tasks that support it.",
        )

    def handle(self, *args, **options) -> None:
        if options["list_targets"]:
            self._list_targets()
            return

        groups = list(options.get("group") or [])
        targets = list(options.get("target") or [])
        if options.get("all"):
            groups.extend(sorted({item.group for item in DOCTOR_TASKS.values()}))
        if not groups and not targets:
            groups = ["core"]

        tasks, unknown = self._resolve_tasks(groups=groups, targets=targets)
        if unknown:
            raise CommandError(f"Unknown doctor target/group selector(s): {', '.join(unknown)}")

        failures = self._run_tasks(tasks, force=bool(options.get("force")))
        if failures:
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("Doctor checks passed."))

    def _list_targets(self) -> None:
        for definition in sorted(DOCTOR_TASKS.values(), key=lambda item: item.target):
            self.stdout.write(f"{definition.target} [group={definition.group}] - {definition.description}")

    def _resolve_tasks(self, *, groups: list[str], targets: list[str]) -> tuple[list[DoctorTaskDefinition], list[str]]:
        resolved: list[DoctorTaskDefinition] = []
        unknown: list[str] = []
        seen_targets: set[str] = set()

        for target in targets:
            definition = DOCTOR_TASKS.get(target)
            if definition is None:
                unknown.append(target)
                continue
            if definition.target not in seen_targets:
                seen_targets.add(definition.target)
                resolved.append(definition)

        available_groups = {item.group for item in DOCTOR_TASKS.values()}
        for group in groups:
            if group not in available_groups:
                unknown.append(group)
                continue
            for definition in sorted(DOCTOR_TASKS.values(), key=lambda item: item.target):
                if definition.group != group:
                    continue
                if definition.target in seen_targets:
                    continue
                seen_targets.add(definition.target)
                resolved.append(definition)

        return resolved, unknown

    def _run_tasks(self, tasks: list[DoctorTaskDefinition], *, force: bool) -> int:
        failures = 0
        for definition in tasks:
            self.stdout.write(self.style.MIGRATE_HEADING(f"[doctor] {definition.target}"))
            if self._run_task(definition, force=force):
                self.stdout.write(self.style.SUCCESS(f"[ok] {definition.target}"))
                continue
            failures += 1
            self.stderr.write(self.style.ERROR(f"[failed] {definition.target}"))
        return failures

    def _run_task(self, definition: DoctorTaskDefinition, *, force: bool) -> bool:
        args = list(definition.command)
        if force and definition.target == "core.health":
            args.append("--force")

        try:
            call_command(*args, stdout=self.stdout, stderr=self.stderr)
        except CommandError as exc:
            self.stderr.write(str(exc))
            return False
        except SystemExit as exc:
            return exc.code in (0, None)
        return True
