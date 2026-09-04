"""Manage optional Arthexis extension repository checkouts."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.repos.services.github import resolve_configured_token
from utils.extensions import (
    DEFAULT_GITHUB_OWNER,
    ExtensionError,
    discover_available_github_extensions,
    extension_key_for_repository,
    install_extension_repository,
    load_declared_extension_repositories,
    load_extension_manifests,
    normalize_github_repository,
    sync_declared_extensions,
    write_declared_extension_repositories,
)


class Command(BaseCommand):
    """List, install, and synchronize extension repositories."""

    help = "Discover and manage optional Arthexis extension repositories."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action", required=True)

        subparsers.add_parser(
            "list",
            help="List configured and checked-out extensions.",
        )

        available = subparsers.add_parser(
            "available",
            help="List GitHub repositories advertised as Arthexis extensions.",
        )
        available.add_argument(
            "--owner",
            default=DEFAULT_GITHUB_OWNER,
            help="GitHub owner whose arthexis-extension repositories should be listed.",
        )

        install = subparsers.add_parser(
            "install",
            help="Install one or more extensions from GitHub.",
        )
        install.add_argument(
            "repositories",
            nargs="+",
            help=(
                "Short names, owner/name slugs, or GitHub URLs. A short name such as "
                "'printers' resolves to arthexis/arthexis-printers."
            ),
        )
        install.add_argument(
            "--owner",
            default=DEFAULT_GITHUB_OWNER,
            help="Default GitHub owner used for short extension names.",
        )
        install.add_argument(
            "--no-save",
            action="store_true",
            help="Clone without adding the repository to extensions/extensions.toml.",
        )

        subparsers.add_parser(
            "sync",
            help="Clone missing declared extensions and fast-forward existing checkouts.",
        )

    def handle(self, *args, **options):
        action = options["action"]
        try:
            if action == "list":
                self._list()
                return
            if action == "available":
                self._available(owner=str(options["owner"]))
                return
            if action == "install":
                self._install(
                    repositories=list(options["repositories"]),
                    owner=str(options["owner"]),
                    save=not bool(options["no_save"]),
                )
                return
            if action == "sync":
                self._sync()
                return
        except ExtensionError as exc:
            raise CommandError(str(exc)) from exc
        raise CommandError(f"Unsupported extensions action: {action}")

    @property
    def base_dir(self) -> Path:
        return Path(settings.BASE_DIR)

    def _list(self) -> None:
        declarations = load_declared_extension_repositories(self.base_dir)
        manifests = load_extension_manifests(self.base_dir)
        installed_by_repository = {
            manifest.repository: manifest for manifest in manifests if manifest.repository
        }
        installed_by_name = {manifest.name: manifest for manifest in manifests}

        if not declarations and not manifests:
            self.stdout.write("No extensions configured or installed.")
            return

        for key, repository in sorted(declarations.items()):
            manifest = installed_by_repository.get(repository) or installed_by_name.get(key)
            state = "installed" if manifest else "missing"
            apps = ",".join(manifest.django_apps) if manifest else "-"
            self.stdout.write(f"{key}\t{state}\t{repository}\t{apps}")

        declared_repositories = set(declarations.values())
        declared_names = set(declarations)
        for manifest in manifests:
            if (
                manifest.repository in declared_repositories
                or manifest.name in declared_names
            ):
                continue
            repository = manifest.repository or "-"
            apps = ",".join(manifest.django_apps)
            self.stdout.write(
                f"{manifest.name}\tinstalled-unmanaged\t{repository}\t{apps}"
            )

    def _available(self, *, owner: str) -> None:
        token = resolve_configured_token()
        extensions = discover_available_github_extensions(owner=owner, token=token)
        if not extensions:
            self.stdout.write(
                f"No repositories with topic 'arthexis-extension' found for {owner}."
            )
            return
        for extension in extensions:
            suffix = f" - {extension.description}" if extension.description else ""
            self.stdout.write(
                f"{extension.name}\t{extension.repository}\t{extension.html_url}{suffix}"
            )

    def _install(
        self,
        *,
        repositories: list[str],
        owner: str,
        save: bool,
    ) -> None:
        declarations = load_declared_extension_repositories(
            self.base_dir,
            include_environment=False,
        )
        for value in repositories:
            repository = normalize_github_repository(value, owner=owner)
            checkout = install_extension_repository(repository, self.base_dir)
            key = extension_key_for_repository(repository)
            self.stdout.write(
                self.style.SUCCESS(f"Installed {repository} -> {checkout}")
            )
            if save:
                declarations[key] = repository

        if save:
            path = write_declared_extension_repositories(
                declarations,
                self.base_dir,
            )
            self.stdout.write(f"Updated {path}")
        self.stdout.write(
            "Run migrations or restart Arthexis so newly installed extension apps are loaded."
        )

    def _sync(self) -> None:
        declarations = load_declared_extension_repositories(self.base_dir)
        if not declarations:
            self.stdout.write("No extensions are declared.")
            return
        checkouts = sync_declared_extensions(self.base_dir)
        for checkout in checkouts:
            self.stdout.write(self.style.SUCCESS(f"Synchronized {checkout.name}"))
        self.stdout.write(
            "Run migrations or restart Arthexis so extension app changes are loaded."
        )
