from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.nginx.management.commands._config_selection import get_configurations
from apps.nginx.models import SiteConfiguration
from apps.nginx.renderers import generate_primary_config, generate_site_entries_content


class Command(BaseCommand):
    help = "Preview the rendered nginx configuration for selected site configurations."  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument(
            "--ids",
            default="",
            help="Comma-separated SiteConfiguration ids to preview.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Preview all site configurations.",
        )

    def handle(self, *args, **options):
        queryset = get_configurations(options["ids"], select_all=options["all"])
        configs = list(queryset)
        if not configs:
            raise CommandError("No site configurations selected. Use --ids or --all.")

        for config in configs:
            self.stdout.write(self.style.MIGRATE_HEADING(f"{config} (id={config.pk})"))
            files = self._build_file_previews(config)
            for preview in files:
                self._write_preview(preview)
            self.stdout.write("")

    def _build_file_previews(self, config: SiteConfiguration) -> list[dict]:
        files: list[dict] = []

        primary_content = generate_primary_config(
            config.mode,
            config.port,
            certificate=config.certificate,
            https_enabled=config.protocol == "https",
            include_ipv6=config.include_ipv6,
            external_websockets=config.external_websockets,
        )
        files.append(
            self._build_file_preview(
                label="Primary configuration",
                path=config.expected_destination,
                content=primary_content,
            )
        )

        try:
            site_content = generate_site_entries_content(
                config.staged_site_config,
                config.mode,
                config.port,
                https_enabled=config.protocol == "https",
                external_websockets=config.external_websockets,
                subdomain_prefixes=config.get_subdomain_prefixes(),
            )
        except ValueError as exc:
            files.append(
                {
                    "label": "Managed site server blocks",
                    "path": config.site_destination_path,
                    "content": "",
                    "status": str(exc),
                }
            )
        else:
            files.append(
                self._build_file_preview(
                    label="Managed site server blocks",
                    path=config.site_destination_path,
                    content=site_content,
                )
            )

        return files

    def _build_file_preview(self, *, label: str, path: Path, content: str) -> dict:
        status = self._get_file_status(path, content)
        return {"label": label, "path": path, "content": content, "status": status}

    def _get_file_status(self, path: Path, content: str) -> str:
        try:
            existing = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "File does not exist on disk."
        except OSError:
            return "Existing file could not be read."

        if existing == content:
            return "Existing file already matches this content."

        return "Existing file differs and would be updated."

    def _write_preview(self, preview: dict) -> None:
        label = preview["label"]
        path = preview["path"]
        status = preview["status"]
        content = preview["content"]

        self.stdout.write(self.style.HTTP_INFO(f"{label}: {path}"))
        self.stdout.write(f"Status: {status}")
        self.stdout.write("-" * 72)
        if content:
            self.stdout.write(content.rstrip())
        else:
            self.stdout.write("(no content)")
        self.stdout.write("\n" + "-" * 72)
