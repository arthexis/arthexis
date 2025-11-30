from django.core.management.base import BaseCommand, CommandError

from ... import release
from ...models import Package as PackageModel


REQUIRED_PACKAGE_FIELDS = (
    "name",
    "description",
    "author",
    "email",
    "python_requires",
    "license",
    "repository_url",
    "homepage_url",
)


class Command(BaseCommand):
    help = "Build the project and optionally upload to PyPI."

    def add_arguments(self, parser):
        parser.add_argument(
            "--bump", action="store_true", help="Increment patch version"
        )
        parser.add_argument("--dist", action="store_true", help="Build distribution")
        parser.add_argument("--twine", action="store_true", help="Upload with Twine")
        parser.add_argument(
            "--git", action="store_true", help="Commit and push changes"
        )
        parser.add_argument(
            "--tag", action="store_true", help="Create and push a git tag"
        )
        parser.add_argument(
            "--test", action="store_true", help="Run tests before building"
        )
        parser.add_argument(
            "--all", action="store_true", help="Enable bump, dist, twine, git and tag"
        )
        parser.add_argument(
            "--force", action="store_true", help="Skip PyPI version check"
        )
        parser.add_argument(
            "--stash", action="store_true", help="Auto stash changes before building"
        )
        parser.add_argument(
            "--package",
            help="Build using the specified package (ID or name)",
        )

    def handle(self, *args, **options):
        package = self._get_package(options.get("package"))
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
                package=package,
            )
        except release.ReleaseError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return 1
        return 0

    def _get_package(self, identifier):
        if not identifier:
            return release.DEFAULT_PACKAGE

        package_obj = self._resolve_package(identifier)
        self._validate_package(package_obj)
        return package_obj.to_package()

    def _resolve_package(self, identifier: str) -> PackageModel:
        query = PackageModel.objects.all()

        try:
            package_obj = query.get(pk=int(identifier))
        except (ValueError, PackageModel.DoesNotExist):
            try:
                package_obj = query.get(name=identifier)
            except PackageModel.DoesNotExist as exc:  # pragma: no cover - safeguard
                raise CommandError(f"Package '{identifier}' not found") from exc
        return package_obj

    def _validate_package(self, package_obj: PackageModel) -> None:
        missing = []
        for field in REQUIRED_PACKAGE_FIELDS:
            value = getattr(package_obj, field)
            if isinstance(value, str):
                value = value.strip()
            if not value:
                missing.append(package_obj._meta.get_field(field).verbose_name)

        if missing:
            readable = ", ".join(missing)
            raise CommandError(
                f"Package '{package_obj.name}' is missing required packaging configuration: {readable}."
            )
