from django.core.management.base import BaseCommand
from ... import release


class Command(BaseCommand):
    help = "Build the project and optionally upload to PyPI."

    def add_arguments(self, parser):
        parser.add_argument("--bump", action="store_true", help="Increment patch version")
        parser.add_argument("--dist", action="store_true", help="Build distribution")
        parser.add_argument("--twine", action="store_true", help="Upload with Twine")
        parser.add_argument("--git", action="store_true", help="Commit and push changes")
        parser.add_argument("--tag", action="store_true", help="Create and push a git tag")
        parser.add_argument("--test", action="store_true", help="Run tests before building")
        parser.add_argument("--all", action="store_true", help="Enable bump, dist, twine, git and tag")
        parser.add_argument("--force", action="store_true", help="Skip PyPI version check")
        parser.add_argument("--stash", action="store_true", help="Auto stash changes before building")

    def handle(self, *args, **options):
        try:
            release.build(
                bump=options["bump"],
                tests=options["test"],
                dist=options["dist"],
                twine=options["twine"],
                git=options["git"],
                tag=options["tag"],
                all=options["all"],
                force=options["force"],
                stash=options["stash"],
            )
        except release.ReleaseError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return 1
        return 0
