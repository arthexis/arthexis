"""Manage local prototype environments and scaffolds."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.core.ui import graphical_env_snapshot, recommended_graphical_env
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
            "--app-module",
            help="Existing installed app module to activate for this prototype.",
        )
        create_parser.add_argument(
            "--app-label",
            help="Optional app label override. Existing apps must match their AppConfig label.",
        )
        create_parser.add_argument(
            "--port",
            type=int,
            help="Backend port reserved for this prototype.",
        )
        create_parser.add_argument(
            "--sqlite-path",
            help="Optional SQLite path override for this prototype.",
        )
        create_parser.add_argument(
            "--sqlite-test-path",
            help="Optional SQLite test database override for this prototype.",
        )
        create_parser.add_argument(
            "--cache-dir",
            help="Optional cache directory override for this prototype.",
        )
        create_parser.add_argument(
            "--isolated-state",
            action="store_true",
            help="Use the prototype's default isolated SQLite and cache directories.",
        )
        create_parser.add_argument(
            "--capture-display-env",
            action="store_true",
            help="Store the current graphical DISPLAY/WAYLAND/XDG env in env_overrides.",
        )
        create_parser.add_argument(
            "--wslg-display",
            action="store_true",
            help="Store recommended WSLg graphical env overrides for this prototype.",
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
        self._ensure_prototype_table()
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
        if options.get("capture_display_env"):
            env_overrides = {**graphical_env_snapshot(), **env_overrides}
        if options.get("wslg_display"):
            wslg_env = recommended_graphical_env()
            if not wslg_env:
                raise CommandError("WSLg graphical environment could not be detected.")
            env_overrides = {**wslg_env, **env_overrides}

        prototype = Prototype(
            slug=slug,
            name=options.get("name") or slug.replace("_", " ").title(),
            description=options.get("description") or "",
            app_module=options.get("app_module") or "",
            app_label=options.get("app_label") or "",
            port=options.get("port") or self._default_port(),
            sqlite_path=options.get("sqlite_path") or "",
            sqlite_test_path=options.get("sqlite_test_path") or "",
            cache_dir=options.get("cache_dir") or "",
            env_overrides=env_overrides,
        )
        self._apply_isolated_state_defaults(prototype, isolated_state=bool(options.get("isolated_state")))
        prototype.save()

        app_dir = prototype_ops.scaffold_prototype_app(prototype)
        self.stdout.write(self.style.SUCCESS(f"Created prototype {prototype.slug}"))
        self.stdout.write(
            f"- mode: {'hidden scaffold' if prototype.uses_hidden_scaffold else 'existing app'}"
        )
        self.stdout.write(f"- app: {prototype.scaffold_module}")
        self.stdout.write(f"- app dir: {app_dir}")
        self.stdout.write(f"- sqlite: {self._describe_runtime_path(prototype.resolved_sqlite_path())}")
        self.stdout.write(
            f"- cache: {self._describe_runtime_path(prototype.resolved_cache_dir())}"
        )

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
                f"{marker} {prototype.slug} | port={prototype.port} | app={prototype.scaffold_module} "
                f"| sqlite={self._describe_runtime_path(prototype.resolved_sqlite_path())}"
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

    @staticmethod
    def _describe_runtime_path(path) -> str:
        return str(path) if path is not None else "current"

    @staticmethod
    def _apply_isolated_state_defaults(prototype: Prototype, *, isolated_state: bool) -> None:
        if not isolated_state:
            return
        if not prototype.sqlite_path:
            prototype.sqlite_path = prototype.default_sqlite_path()
        if not prototype.sqlite_test_path:
            prototype.sqlite_test_path = prototype.default_sqlite_test_path()
        if not prototype.cache_dir:
            prototype.cache_dir = prototype.default_cache_dir()

    @staticmethod
    def _ensure_prototype_table() -> None:
        with connection.cursor() as cursor:
            table_names = connection.introspection.table_names(cursor)
        if Prototype._meta.db_table in table_names:
            return
        raise CommandError(
            "Prototype records are not migrated yet. Run `python manage.py migrate prototypes` first."
        )
