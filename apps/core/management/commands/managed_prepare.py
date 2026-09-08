from django.core.management.base import BaseCommand, CommandError

from apps.core.system.lifecycle import managed_layout, prepare_managed_install


class Command(BaseCommand):
    help = "Prepare dependencies and Django state for a GWAY-managed checkout"

    def add_arguments(self, parser):
        parser.add_argument("--root", default=None)
        parser.add_argument("--editable", action="store_true")
        parser.add_argument("--no-migrate", action="store_true")
        parser.add_argument("--no-collectstatic", action="store_true")

    def handle(self, *args, **options):
        layout = managed_layout(options["root"])
        try:
            prepare_managed_install(
                layout=layout,
                editable=options["editable"],
                run_migrations=not options["no_migrate"],
                run_collectstatic=not options["no_collectstatic"],
            )
        except (FileNotFoundError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(str(layout.root))
