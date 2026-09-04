"""Discovery and checkout helpers for optional Arthexis extension repositories."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

try:  # pragma: no cover - Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


EXTENSION_MANIFEST_FILENAME = "arthexis-extension.toml"
EXTENSION_CONFIG_FILENAME = "extensions.toml"
DEFAULT_GITHUB_OWNER = "arthexis"
GITHUB_API_ROOT = "https://api.github.com"
GITHUB_REQUEST_TIMEOUT = 10
_GITHUB_REPOSITORY_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_EXTENSION_REPOSITORY_ENV_NAMES = (
    "ARTHEXIS_EXTENSIONS",
    "ARTHEXIS_EXTENSION_REPOS",
)


class ExtensionError(RuntimeError):
    """Raised when an extension declaration or checkout cannot be processed."""


@dataclass(frozen=True)
class ExtensionManifest:
    """Static metadata declared by one checked-out extension repository."""

    name: str
    path: Path
    repository: str
    django_apps: tuple[str, ...]
    requires_apps: tuple[str, ...] = ()
    feature_packs: tuple[str, ...] = ()
    suite_features: tuple[str, ...] = ()


@dataclass(frozen=True)
class AvailableExtension:
    """GitHub repository advertised as an Arthexis extension."""

    name: str
    repository: str
    description: str = ""
    html_url: str = ""


def _default_base_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def extensions_root(base_dir: str | Path | None = None) -> Path:
    """Return the managed extension checkout directory."""

    root = Path(base_dir) if base_dir is not None else _default_base_dir()
    return root / "extensions"


def extension_config_path(base_dir: str | Path | None = None) -> Path:
    """Return the extension declaration file path."""

    return extensions_root(base_dir) / EXTENSION_CONFIG_FILENAME


def _load_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ExtensionError(f"Unable to read extension metadata from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExtensionError(f"{path}: expected a TOML table.")
    return payload


def _string_tuple(value: object, *, field_name: str, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ExtensionError(f"{path}: {field_name} must be a list of strings.")
    values: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ExtensionError(f"{path}: {field_name} must be a list of strings.")
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            values.append(cleaned)
            seen.add(cleaned)
    return tuple(values)


def _parse_extension_manifest(path: Path) -> ExtensionManifest:
    payload = _load_toml(path)
    metadata = payload.get("extension", payload)
    if not isinstance(metadata, dict):
        raise ExtensionError(f"{path}: [extension] must be a TOML table.")

    name = str(metadata.get("name") or "").strip()
    repository = str(metadata.get("repository") or "").strip()
    if not name:
        raise ExtensionError(f"{path}: extension.name is required.")

    django_apps = _string_tuple(
        metadata.get("django_apps"),
        field_name="extension.django_apps",
        path=path,
    )
    if not django_apps:
        raise ExtensionError(f"{path}: extension.django_apps must contain at least one app.")

    return ExtensionManifest(
        name=name,
        path=path,
        repository=repository,
        django_apps=django_apps,
        requires_apps=_string_tuple(
            metadata.get("requires_apps"),
            field_name="extension.requires_apps",
            path=path,
        ),
        feature_packs=_string_tuple(
            metadata.get("feature_packs"),
            field_name="extension.feature_packs",
            path=path,
        ),
        suite_features=_string_tuple(
            metadata.get("suite_features"),
            field_name="extension.suite_features",
            path=path,
        ),
    )


def load_extension_manifests(
    base_dir: str | Path | None = None,
) -> tuple[ExtensionManifest, ...]:
    """Return valid manifests from immediate children of ``extensions/``."""

    root = extensions_root(base_dir)
    if not root.exists():
        return ()

    manifests: list[ExtensionManifest] = []
    seen_names: dict[str, Path] = {}
    seen_apps: dict[str, Path] = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / EXTENSION_MANIFEST_FILENAME
        if not manifest_path.is_file():
            continue
        manifest = _parse_extension_manifest(manifest_path)
        previous_name = seen_names.setdefault(manifest.name, manifest_path)
        if previous_name != manifest_path:
            raise ExtensionError(
                f"{manifest_path}: extension name {manifest.name!r} is already declared "
                f"by {previous_name}."
            )
        for app_entry in manifest.django_apps:
            previous_app = seen_apps.setdefault(app_entry, manifest_path)
            if previous_app != manifest_path:
                raise ExtensionError(
                    f"{manifest_path}: Django app {app_entry!r} is already declared "
                    f"by {previous_app}."
                )
        manifests.append(manifest)
    return tuple(manifests)


def activate_extension_paths(
    base_dir: str | Path | None = None,
) -> tuple[ExtensionManifest, ...]:
    """Place checked-out extension roots on ``sys.path`` and return their manifests."""

    manifests = load_extension_manifests(base_dir)
    for checkout in reversed(tuple(dict.fromkeys(m.path.parent for m in manifests))):
        checkout_text = str(checkout)
        if checkout_text not in sys.path:
            sys.path.insert(0, checkout_text)
    return manifests


def load_extension_django_apps(
    base_dir: str | Path | None = None,
) -> tuple[str, ...]:
    """Return Django app entries declared by installed extensions."""

    return tuple(
        dict.fromkeys(
            app_entry
            for manifest in load_extension_manifests(base_dir)
            for app_entry in manifest.django_apps
        )
    )


def load_extension_app_dependency_metadata(
    base_dir: str | Path | None = None,
) -> dict[str, tuple[str, ...]]:
    """Return per-app dependency metadata declared by installed extensions."""

    dependencies: dict[str, tuple[str, ...]] = {}
    for manifest in load_extension_manifests(base_dir):
        for app_entry in manifest.django_apps:
            dependencies[app_entry] = manifest.requires_apps
    return dependencies


def _split_repository_setting(value: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r"[,;\s]+", value) if part)


def _validate_github_repository_part(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    if (
        cleaned in {".", ".."}
        or not cleaned
        or not _GITHUB_REPOSITORY_PART_RE.fullmatch(cleaned)
    ):
        raise ExtensionError(f"Invalid GitHub {field_name}: {value!r}")
    return cleaned


def normalize_github_repository(
    value: str,
    *,
    owner: str = DEFAULT_GITHUB_OWNER,
) -> str:
    """Normalize a short extension name, repository slug, or GitHub URL."""

    cleaned = str(value or "").strip()
    if not cleaned:
        raise ExtensionError("Extension repository cannot be empty.")

    if "://" in cleaned:
        parsed = urlparse(cleaned)
        if parsed.hostname not in {"github.com", "www.github.com"}:
            raise ExtensionError("Extension repositories must be hosted on GitHub.")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ExtensionError(f"Invalid GitHub repository URL: {cleaned}")
        repository_owner, repository_name = parts[:2]
        repository_owner = _validate_github_repository_part(
            repository_owner,
            field_name="owner",
        )
        repository_name = _validate_github_repository_part(
            repository_name.removesuffix(".git"),
            field_name="repository name",
        )
        return f"{repository_owner}/{repository_name}"

    if "/" in cleaned:
        parts = [part for part in cleaned.split("/") if part]
        if len(parts) != 2:
            raise ExtensionError("Repository must use owner/name format.")
        repository_owner = _validate_github_repository_part(
            parts[0],
            field_name="owner",
        )
        repository_name = _validate_github_repository_part(
            parts[1].removesuffix(".git"),
            field_name="repository name",
        )
        return f"{repository_owner}/{repository_name}"

    repository_name = cleaned
    if not repository_name.startswith("arthexis-"):
        repository_name = f"arthexis-{repository_name}"
    repository_owner = _validate_github_repository_part(owner, field_name="owner")
    repository_name = _validate_github_repository_part(
        repository_name,
        field_name="repository name",
    )
    return f"{repository_owner}/{repository_name}"


def extension_key_for_repository(repository: str) -> str:
    """Return the short configuration key for a normalized repository slug."""

    name = repository.rsplit("/", 1)[-1]
    return name.removeprefix("arthexis-") or name


def load_declared_extension_repositories(
    base_dir: str | Path | None = None,
    *,
    include_environment: bool = True,
) -> dict[str, str]:
    """Return configured extension repositories keyed by short extension name."""

    declarations: dict[str, str] = {}
    path = extension_config_path(base_dir)
    if path.is_file():
        payload = _load_toml(path)
        configured = payload.get("extensions", {})
        if not isinstance(configured, dict):
            raise ExtensionError(f"{path}: [extensions] must be a TOML table.")
        for key, value in configured.items():
            if not isinstance(value, str):
                raise ExtensionError(
                    f"{path}: extensions.{key} must be a GitHub repository string."
                )
            declarations[str(key).strip()] = normalize_github_repository(value)

    if include_environment:
        for env_name in _EXTENSION_REPOSITORY_ENV_NAMES:
            for value in _split_repository_setting(os.environ.get(env_name, "")):
                repository = normalize_github_repository(value)
                declarations.setdefault(extension_key_for_repository(repository), repository)
    return declarations


def write_declared_extension_repositories(
    repositories: dict[str, str],
    base_dir: str | Path | None = None,
) -> Path:
    """Write the canonical local extension declaration file."""

    path = extension_config_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Managed Arthexis extension repositories.",
        "# Edit this table and run `python manage.py extensions sync`.",
        "",
        "[extensions]",
    ]
    for key, repository in sorted(repositories.items()):
        lines.append(f"{key} = {json.dumps(normalize_github_repository(repository))}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _run_git(args: list[str], *, cwd: Path | None = None) -> None:
    try:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise ExtensionError("git is required to manage extension repositories.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise ExtensionError(message) from exc


def install_extension_repository(
    repository: str,
    base_dir: str | Path | None = None,
    *,
    update: bool = False,
) -> Path:
    """Clone one GitHub extension or fast-forward an existing managed checkout."""

    normalized = normalize_github_repository(repository)
    checkout_name = normalized.rsplit("/", 1)[-1]
    root = extensions_root(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    checkout = root / checkout_name

    if checkout.exists():
        if not (checkout / ".git").exists():
            raise ExtensionError(
                f"{checkout} already exists but is not a Git repository checkout."
            )
        if update:
            _run_git(["pull", "--ff-only"], cwd=checkout)
    else:
        _run_git(
            [
                "clone",
                "--depth",
                "1",
                f"https://github.com/{normalized}.git",
                str(checkout),
            ]
        )

    manifest_path = checkout / EXTENSION_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ExtensionError(
            f"{normalized} does not contain {EXTENSION_MANIFEST_FILENAME}."
        )
    _parse_extension_manifest(manifest_path)
    return checkout


def sync_declared_extensions(
    base_dir: str | Path | None = None,
) -> tuple[Path, ...]:
    """Clone or fast-forward every extension declared in extensions.toml/environment."""

    declarations = load_declared_extension_repositories(base_dir)
    return tuple(
        install_extension_repository(repository, base_dir, update=True)
        for repository in declarations.values()
    )


def discover_available_github_extensions(
    *,
    owner: str = DEFAULT_GITHUB_OWNER,
    token: str | None = None,
) -> tuple[AvailableExtension, ...]:
    """List GitHub repositories advertised with the ``arthexis-extension`` topic."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "arthexis-extensions",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        response = requests.get(
            f"{GITHUB_API_ROOT}/search/repositories",
            headers=headers,
            params={
                "q": f"user:{owner} topic:arthexis-extension",
                "sort": "name",
                "order": "asc",
                "per_page": 100,
            },
            timeout=GITHUB_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ExtensionError(str(exc)) from exc

    try:
        if not 200 <= response.status_code < 300:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            message = (
                payload.get("message")
                if isinstance(payload, dict)
                else None
            ) or response.text or "GitHub extension discovery failed."
            raise ExtensionError(str(message))

        try:
            payload = response.json()
        except ValueError as exc:
            raise ExtensionError("Unable to decode GitHub extension search response.") from exc
        items = payload.get("items", []) if isinstance(payload, dict) else []
        extensions: list[AvailableExtension] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            full_name = str(item.get("full_name") or "").strip()
            repository_name = str(item.get("name") or "").strip()
            if not full_name or not repository_name:
                continue
            extensions.append(
                AvailableExtension(
                    name=repository_name.removeprefix("arthexis-"),
                    repository=full_name,
                    description=str(item.get("description") or "").strip(),
                    html_url=str(item.get("html_url") or "").strip(),
                )
            )
        return tuple(extensions)
    finally:
        response.close()
