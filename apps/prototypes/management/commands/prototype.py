"""Manage local prototype environments and scaffolds."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.prototypes import prototype_ops
from apps.prototypes.models import Prototype


class Command(BaseCommand):
    """Manage prototype records, scaffolds, and activation state."""

    help = "Create, activate, deactivate, and inspect local prototype environments."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action")
        subparsers.required = True

        create_parser = subparsers.add_parser(
            "create",
            help="Create a prototype record and hidden app scaffold.",
        )
        create_parser.add_argument("slug", help="Prototype slug in lowercase snake_case.")
        create_parser.add_argument("--name", help="Display name. Defaults to a title-cased slug.")
        create_parser.add_argument("--description", default="", help="Optional description text.")
        create_parser.add_argument(
            "--port",
            type=int,
            help="Backend port reserved for this prototype.",
        )
        create_parser.add_argument(
            "--set",
            nargs=2,
            action="append",
            metavar=("KEY", "VALUE"),
            help="Prototype-only environment override (repeatable).",
        )
        create_parser.add_argument(
            "--activate",
            action="store_true",
            help="Activate the prototype after creation.",
        )
        create_parser.add_argument(
            "--no-restart",
            action="store_true",
            help="Do not restart after activation.",
        )
        create_parser.add_argument(
            "--force",
            action="store_true",
            help="Pass --force to stop.sh when restarting after activation.",
        )

        activate_parser = subparsers.add_parser(
            "activate",
            help="Activate an existing prototype and optionally restart the suite.",
        )
        activate_parser.add_argument("slug", help="Prototype slug to activate.")
        activate_parser.add_argument(
            "--no-restart",
            action="store_true",
            help="Only update env/lock state; do not restart the suite.",
        )
        activate_parser.add_argument(
            "--force",
            action="store_true",
            help="Pass --force to stop.sh when restarting.",
        )

        deactivate_parser = subparsers.add_parser(
            "deactivate",
            help="Clear the active prototype and optionally restart the suite.",
        )
        deactivate_parser.add_argument(
            "--no-restart",
            action="store_true",
            help="Only clear env/lock state; do not restart the suite.",
        )
        deactivate_parser.add_argument(
            "--force",
            action="store_true",
            help="Pass --force to stop.sh when restarting.",
        )

        subparsers.add_parser(
            "status",
            help="Show the current activation state and registered prototypes.",
        )

    def handle(self, *args, **options):
        action = options["action"]
        handler = getattr(self, f"_handle_{action}", None)
        if handler is None:
            raise CommandError(f"Unsupported prototype action: {action}")
        return handler(**options)

    def _handle_create(self, **options):
        slug = str(options["slug"]).strip()
        if Prototype.objects.filter(slug=slug).exists():
            raise CommandError(f"Prototype already exists: {slug}")

        env_overrides = dict(options.get("set") or [])
        prototype = Prototype(
            slug=slug,
            name=options.get("name") or slug.replace("_", " ").title(),
            description=options.get("description") or "",
            port=options.get("port") or self._default_port(),
            env_overrides=env_overrides,
        )
        prototype.save()

        app_dir = prototype_ops.scaffold_prototype_app(prototype)
        self.stdout.write(self.style.SUCCESS(f"Created prototype {prototype.slug}"))
        self.stdout.write(f"- app: {prototype.scaffold_module}")
        self.stdout.write(f"- scaffold: {app_dir}")
        self.stdout.write(f"- sqlite: {prototype.resolved_sqlite_path()}")
        self.stdout.write(f"- cache: {prototype.resolved_cache_dir()}")

        if options.get("activate"):
            self._activate_and_maybe_restart(
                prototype,
                restart=not options.get("no_restart"),
                force_stop=bool(options.get("force")),
            )
        else:
            self.stdout.write(
                "Run `python manage.py prototype activate "
                f"{prototype.slug}` to switch into it."
            )

    def _handle_activate(self, **options):
        slug = str(options["slug"]).strip()
        prototype = Prototype.objects.filter(slug=slug).first()
        if prototype is None:
            raise CommandError(f"Prototype not found: {slug}")
        self._activate_and_maybe_restart(
            prototype,
            restart=not options.get("no_restart"),
            force_stop=bool(options.get("force")),
        )

    def _handle_deactivate(self, **options):
        prototype_ops.deactivate_prototype()
        self.stdout.write(self.style.SUCCESS("Cleared the active prototype."))
        if options.get("no_restart"):
            self.stdout.write("Restart skipped.")
            return
        prototype_ops.restart_suite(force_stop=bool(options.get("force")))
        self.stdout.write(self.style.SUCCESS("Suite restarted without a prototype overlay."))

    def _handle_status(self, **_options):
        active = Prototype.objects.filter(is_active=True).order_by("name", "slug").first()
        if active is None:
            self.stdout.write("Active prototype: none")
        else:
            self.stdout.write(f"Active prototype: {active.slug} ({active.name})")

        rows = list(Prototype.objects.order_by("-is_active", "name", "slug"))
        if not rows:
            self.stdout.write("No prototypes registered.")
            return

        for prototype in rows:
            marker = "*" if prototype.is_active else "-"
            self.stdout.write(
                f"{marker} {prototype.slug} | port={prototype.port} | app={prototype.scaffold_module}"
            )

    def _activate_and_maybe_restart(
        self,
        prototype: Prototype,
        *,
        restart: bool,
        force_stop: bool,
    ) -> None:
        prototype_ops.scaffold_prototype_app(prototype)
        prototype_ops.activate_prototype(prototype)
        self.stdout.write(self.style.SUCCESS(f"Activated prototype {prototype.slug}."))
        self.stdout.write(f"- env: {prototype_ops.env_path()}")
        self.stdout.write(f"- port lock: {prototype_ops.backend_port_lock_path()}")
        if not restart:
            self.stdout.write("Restart skipped.")
            return
        prototype_ops.restart_suite(force_stop=force_stop)
        self.stdout.write(self.style.SUCCESS("Suite restarted with the active prototype."))

    @staticmethod
    def _default_port() -> int:
        highest_port = (
            Prototype.objects.order_by("-port").values_list("port", flat=True).first()
        )
        if highest_port is None:
            return 8890
        return max(int(highest_port) + 1, 8890)
