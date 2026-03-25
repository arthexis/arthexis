"""Rebrand repository content while preserving Arthexis licensing."""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

LICENSE_ACKNOWLEDGEMENT = "I ACKNOWLEDGE THE ARTHEXIS LICENSE"
DEFAULT_REPO_OWNER = "arthexis"
LICENSE_REF_PLACEHOLDER = "__REBRAND_LICENSE_REF__"
URL_GIT_PLACEHOLDER = "__REBRAND_GITHUB_URL_GIT__"
URL_PLACEHOLDER = "__REBRAND_GITHUB_URL__"
URL_SLUG_PLACEHOLDER = "__REBRAND_GITHUB_SLUG__"
TEXT_SUFFIX_ALLOWLIST = {
    ".bat",
    ".cfg",
    ".conf",
    ".css",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".po",
    ".py",
    ".sample",
    ".service",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
SEED_DATA_PATTERNS = (
    "apps/core/fixtures/*__arthexis.json",
    "apps/core/fixtures/releases__packagerelease_*.json",
    "apps/release/fixtures/*__arthexis.json",
    "apps/repos/fixtures/*__arthexis.json",
)


class Command(BaseCommand):
    """Apply a safe template-style rebrand of Arthexis to a new distribution name."""

    help = (
        "Rebrand this repository for templated reuse while keeping the Arthexis "
        "license in place."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "name",
            nargs="?",
            help="Primary replacement for the lowercase token 'arthexis'.",
        )
        parser.add_argument(
            "--service-name",
            help="Override service/unit naming (defaults to --name).",
        )
        parser.add_argument(
            "--repo-name",
            help="Override repository name in GitHub URLs (defaults to --name).",
        )
        parser.add_argument(
            "--repo-owner",
            default=DEFAULT_REPO_OWNER,
            help="Repository owner to use in GitHub URLs.",
        )
        parser.add_argument(
            "--python-package",
            help="Override Python package token (defaults to --name with '-' converted to '_').",
        )
        parser.add_argument(
            "--base-dir",
            help="Override repository root for rebrand operations.",
        )
        parser.add_argument(
            "--acknowledge-license",
            action="store_true",
            help="Acknowledge the Arthexis License without interactive prompt.",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Fail instead of prompting for missing values/acknowledgement.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without writing files.",
        )

    def handle(self, *args, **options):
        base_dir = Path(options.get("base_dir") or settings.BASE_DIR).resolve()
        primary_name = self._resolve_name(
            name_option=options.get("name"),
            no_input=bool(options.get("no_input")),
        )
        service_name = self._normalize_machine_name(options.get("service_name") or primary_name, "service name")
        repo_name = self._normalize_machine_name(options.get("repo_name") or primary_name, "repository name")
        repo_owner = self._normalize_machine_name(options.get("repo_owner") or DEFAULT_REPO_OWNER, "repository owner")
        python_package = self._normalize_python_package(
            options.get("python_package") or primary_name.replace("-", "_")
        )
        acknowledged = self._resolve_license_acknowledgement(
            already_acknowledged=bool(options.get("acknowledge_license")),
            no_input=bool(options.get("no_input")),
        )
        if not acknowledged:
            raise CommandError("Cannot proceed without acknowledging the Arthexis License.")

        dry_run = bool(options.get("dry_run"))
        changed_files = self._replace_tokens(
            base_dir=base_dir,
            primary_name=primary_name,
            repo_name=repo_name,
            repo_owner=repo_owner,
            service_name=service_name,
            python_package=python_package,
            dry_run=dry_run,
        )
        removed_seed_files = self._remove_seed_data(base_dir=base_dir, dry_run=dry_run)

        action = "Would update" if dry_run else "Updated"
        removal_action = "Would remove" if dry_run else "Removed"
        self.stdout.write(self.style.SUCCESS(f"{action} {len(changed_files)} file(s) with rebrand replacements."))
        self.stdout.write(self.style.SUCCESS(f"{removal_action} {len(removed_seed_files)} seed fixture file(s)."))

        self.stdout.write("\nRebrand summary:")
        self.stdout.write(f"- base name: {primary_name}")
        self.stdout.write(f"- service name: {service_name}")
        self.stdout.write(f"- repository slug: {repo_owner}/{repo_name}")
        self.stdout.write(f"- python package token: {python_package}")
        self.stdout.write("- license: Arthexis License preserved (LICENSE left untouched)")

    def _resolve_name(self, *, name_option: str | None, no_input: bool) -> str:
        if name_option:
            return self._normalize_machine_name(name_option, "name")
        if no_input:
            raise CommandError("name is required when --no-input is set")
        entered = input("Enter the new primary project name (machine-safe): ").strip()
        if not entered:
            raise CommandError("A non-empty project name is required")
        return self._normalize_machine_name(entered, "name")

    def _resolve_license_acknowledgement(self, *, already_acknowledged: bool, no_input: bool) -> bool:
        if already_acknowledged:
            return True
        if no_input:
            return False
        self.stdout.write("\nArthexis License notice:")
        self.stdout.write("The Arthexis License remains in effect after rebranding.")
        response = input(
            f"Type exactly '{LICENSE_ACKNOWLEDGEMENT}' to continue: "
        ).strip()
        return response == LICENSE_ACKNOWLEDGEMENT

    def _replace_tokens(
        self,
        *,
        base_dir: Path,
        primary_name: str,
        repo_name: str,
        repo_owner: str,
        service_name: str,
        python_package: str,
        dry_run: bool,
    ) -> list[Path]:
        changed_files: list[Path] = []
        project_display_name = primary_name.replace("-", " ").replace("_", " ").title()
        normalized_primary_package = primary_name.replace("-", "_")

        for path in self._iter_candidate_files(base_dir):
            original = path.read_text(encoding="utf-8")
            rewritten = original

            rewritten = rewritten.replace(
                "LicenseRef-Arthexis",
                LICENSE_REF_PLACEHOLDER,
            )
            rewritten = rewritten.replace(
                "https://github.com/arthexis/arthexis.git",
                URL_GIT_PLACEHOLDER,
            )
            rewritten = rewritten.replace(
                "https://github.com/arthexis/arthexis",
                URL_PLACEHOLDER,
            )
            rewritten = rewritten.replace(
                "github.com/arthexis/arthexis",
                URL_SLUG_PLACEHOLDER,
            )

            rewritten = rewritten.replace("ARTHEXIS", primary_name.upper().replace("-", "_").replace(" ", "_"))
            rewritten = rewritten.replace("Arthexis", project_display_name)

            if python_package != normalized_primary_package or "-" in primary_name:
                rewritten = self._replace_python_package_tokens(
                    rewritten,
                    source_package="arthexis",
                    python_package=python_package,
                )
            rewritten = rewritten.replace("arthexis", primary_name)

            if service_name != primary_name:
                rewritten = self._replace_service_tokens(rewritten, primary_name=primary_name, service_name=service_name)
            rewritten = rewritten.replace(
                URL_GIT_PLACEHOLDER,
                f"https://github.com/{repo_owner}/{repo_name}.git",
            )
            rewritten = rewritten.replace(
                URL_PLACEHOLDER,
                f"https://github.com/{repo_owner}/{repo_name}",
            )
            rewritten = rewritten.replace(
                URL_SLUG_PLACEHOLDER,
                f"github.com/{repo_owner}/{repo_name}",
            )
            rewritten = rewritten.replace(
                LICENSE_REF_PLACEHOLDER,
                "LicenseRef-Arthexis",
            )

            if rewritten == original:
                continue

            changed_files.append(path)
            if not dry_run:
                path.write_text(rewritten, encoding="utf-8")

        return changed_files

    def _replace_service_tokens(self, content: str, *, primary_name: str, service_name: str) -> str:
        return (
            content.replace(f"--name {primary_name}", f"--name {service_name}")
            .replace(f"service {primary_name}", f"service {service_name}")
            .replace(f"/{primary_name}.service", f"/{service_name}.service")
            .replace(f"{primary_name}.service", f"{service_name}.service")
        )

    def _replace_python_package_tokens(self, content: str, *, source_package: str, python_package: str) -> str:
        return (
            content.replace(f"import {source_package}", f"import {python_package}")
            .replace(f"\"{source_package}\"", f"\"{python_package}\"")
            .replace(f"'{source_package}'", f"'{python_package}'")
        )

    def _remove_seed_data(self, *, base_dir: Path, dry_run: bool) -> list[Path]:
        removed: list[Path] = []
        for pattern in SEED_DATA_PATTERNS:
            for candidate in sorted(base_dir.glob(pattern)):
                if not candidate.is_file():
                    continue
                removed.append(candidate)
                if not dry_run:
                    candidate.unlink()
        return removed

    def _iter_candidate_files(self, base_dir: Path):
        for path in base_dir.rglob("*"):
            if path.is_symlink():
                continue
            if not path.is_file() or path.name == "LICENSE":
                continue
            if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
                continue
            if path.suffix.lower() not in TEXT_SUFFIX_ALLOWLIST:
                continue
            yield path

    def _normalize_machine_name(self, value: str, label: str) -> str:
        cleaned = str(value).strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", cleaned):
            raise CommandError(
                f"Invalid {label}: '{value}'. Use lowercase letters, numbers, hyphen, or underscore, and start with a letter."
            )
        return cleaned

    def _normalize_python_package(self, value: str) -> str:
        cleaned = str(value).strip().replace("-", "_").lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", cleaned):
            raise CommandError(
                f"Invalid python package token: '{value}'. Use lowercase letters, numbers, and underscore only."
            )
        return cleaned
