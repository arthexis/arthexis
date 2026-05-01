from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.db import transaction

from apps.skills.models import AgentSkill, AgentSkillFile

PACKAGE_FORMAT = "arthexis.codex_skill_package.v1"
SKILL_MARKDOWN = "SKILL.md"
EMPTY_CONTENT_SHA256 = hashlib.sha256(b"").hexdigest()
DEFAULT_MATERIALIZE_SIGIL_ROOTS = frozenset({"NODE", "SYS"})
SAFE_MATERIALIZE_CONF_KEYS = frozenset({"BASE_DIR", "NODE_ROLE"})
CONF_DOT_SIGIL_RE = re.compile(r"\[CONF\.([A-Za-z0-9_-]+)\]")

BLOCKED_STATE_FILENAMES = {
    "pending-approval-alerts.lock.json",
    "security-codes.json",
    "standard-materials.db",
    "todo.md",
    "workgroup.md",
}
BLOCKED_CACHE_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "cache",
    "logs",
    "node_modules",
    "sessions",
}
BLOCKED_SECRET_DIRS = {"credentials", "secrets", "tokens"}
SECRET_NAME_FRAGMENTS = ("credential", "password", "secret", "token")
GENERATED_REFERENCE_DIRS = {"mtg-rules"}
PORTABLE_ROOTS = {"agents", "assets", "references", "scripts", "templates"}
OPERATOR_PATH_MARKERS = (
    "C:\\Users\\",
    "C:/Users/",
    "/home/",
    "/Users/",
    ".codex\\",
    ".codex/",
)


@dataclass(frozen=True)
class SkillFileClassification:
    portability: str
    included_by_default: bool
    exclusion_reason: str = ""


@dataclass(frozen=True)
class SkillFileScan:
    relative_path: str
    portability: str
    included_by_default: bool
    exclusion_reason: str
    size_bytes: int
    content_sha256: str


def normalize_package_path(path: Path) -> str:
    return path.as_posix()


def validate_package_relative_path(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(relative_path)
    if (
        not normalized
        or normalized == "."
        or path.is_absolute()
        or Path(relative_path).is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"Unsafe package path: {relative_path}")
    return path.as_posix()


def validate_package_skill_slug(slug: str) -> str:
    if not isinstance(slug, str) or not slug:
        raise ValueError(f"Unsafe skill slug: {slug}")
    try:
        validate_slug(slug)
    except ValidationError as error:
        raise ValueError(f"Unsafe skill slug: {slug}") from error
    return slug


def _resolve_document_sigils(
    content: str,
    *,
    resolve_sigils_on_write: bool,
    allowed_roots: set[str] | frozenset[str] | None,
) -> str:
    if not resolve_sigils_on_write:
        return content

    from apps.sigils.sigil_resolver import resolve_sigils

    return resolve_sigils(
        _resolve_safe_conf_sigils(content),
        allowed_roots=allowed_roots,
    )


def _resolve_safe_conf_sigils(content: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw_key = match.group(1)
        normalized_key = raw_key.replace("-", "_").upper()
        if normalized_key not in SAFE_MATERIALIZE_CONF_KEYS:
            return match.group(0)
        for candidate in (raw_key, normalized_key, normalized_key.lower()):
            sentinel = object()
            value = getattr(settings, candidate, sentinel)
            if value is not sentinel:
                return str(value)
        return ""

    return CONF_DOT_SIGIL_RE.sub(replace, content)


def _remove_tree_best_effort(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return
        if path.is_dir():
            for nested in sorted(path.rglob("*"), reverse=True):
                if nested.is_file() or nested.is_symlink():
                    nested.unlink()
                elif nested.is_dir():
                    nested.rmdir()
            path.rmdir()
    except OSError:
        return


def _should_prune_materialized_path(
    relative_path: str, desired_paths: set[str]
) -> bool:
    if relative_path in desired_paths:
        return False
    classification = classify_codex_skill_path(relative_path)
    if classification is not None and not classification.included_by_default:
        return False
    top_level = relative_path.split("/", 1)[0]
    return relative_path == SKILL_MARKDOWN or top_level in PORTABLE_ROOTS


def _prune_stale_materialized_files(
    skill_dir: Path,
    desired_paths: set[str],
) -> None:
    for existing_path in sorted(skill_dir.rglob("*"), reverse=True):
        try:
            if existing_path.is_file() or existing_path.is_symlink():
                relative = normalize_package_path(existing_path.relative_to(skill_dir))
                if _should_prune_materialized_path(relative, desired_paths):
                    existing_path.unlink()
            elif existing_path.is_dir() and not any(existing_path.iterdir()):
                existing_path.rmdir()
        except OSError:
            continue


def _prepare_materialized_file_path(path: Path, skill_dir: Path) -> None:
    current = skill_dir
    for part in path.relative_to(skill_dir).parts[:-1]:
        current = current / part
        if current.is_symlink() or current.is_file():
            current.unlink()

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        _remove_tree_best_effort(path)


def _validate_materialized_package_paths(package_paths: list[str]) -> None:
    unique_paths = set(package_paths)
    if len(unique_paths) != len(package_paths):
        raise ValueError("Duplicate package path")
    for package_path in unique_paths:
        parts = package_path.split("/")
        for index in range(1, len(parts)):
            prefix = "/".join(parts[:index])
            if prefix in unique_paths:
                raise ValueError(
                    f"Package path collides with nested path: {package_path}"
                )


def _normalized_parts(relative_path: str) -> tuple[str, list[str]]:
    normalized = relative_path.replace("\\", "/")
    return normalized, [part.lower() for part in normalized.split("/")]


def classify_codex_skill_path(relative_path: str) -> SkillFileClassification | None:
    _, parts = _normalized_parts(relative_path)
    filename = parts[-1]
    suffix = Path(filename).suffix.lower()

    if filename in BLOCKED_STATE_FILENAMES:
        return SkillFileClassification(
            AgentSkillFile.Portability.STATE,
            False,
            "runtime state is not portable",
        )
    if any(part in BLOCKED_SECRET_DIRS for part in parts):
        return SkillFileClassification(
            AgentSkillFile.Portability.SECRET,
            False,
            "credential directory is not portable",
        )
    if any(fragment in filename for fragment in SECRET_NAME_FRAGMENTS):
        return SkillFileClassification(
            AgentSkillFile.Portability.SECRET,
            False,
            "credential-like filename is not portable",
        )
    if any(part in BLOCKED_CACHE_DIRS for part in parts):
        return SkillFileClassification(
            AgentSkillFile.Portability.CACHE,
            False,
            "cache, log, or generated runtime directory is not portable",
        )
    if any(part in GENERATED_REFERENCE_DIRS for part in parts):
        return SkillFileClassification(
            AgentSkillFile.Portability.GENERATED_REFERENCE,
            False,
            "generated reference archive should be refreshed on the target",
        )
    if suffix in {".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".log", ".tmp"}:
        return SkillFileClassification(
            AgentSkillFile.Portability.STATE,
            False,
            "runtime artifact file is not portable",
        )
    return None


def classify_codex_skill_file(
    relative_path: str,
    content: str | None,
) -> SkillFileClassification:
    normalized, parts = _normalized_parts(relative_path)
    path_classification = classify_codex_skill_path(relative_path)
    if path_classification is not None:
        return path_classification
    if content is None:
        return SkillFileClassification(
            AgentSkillFile.Portability.DEVICE_SCOPED,
            False,
            "binary files are not exported by this prototype",
        )
    if normalized == SKILL_MARKDOWN:
        return SkillFileClassification(AgentSkillFile.Portability.PORTABLE, True)
    if any(marker in content for marker in OPERATOR_PATH_MARKERS):
        return SkillFileClassification(
            AgentSkillFile.Portability.OPERATOR_SCOPED,
            False,
            "operator-local paths must be parameterized before export",
        )
    if parts[0] in PORTABLE_ROOTS:
        return SkillFileClassification(AgentSkillFile.Portability.PORTABLE, True)
    return SkillFileClassification(
        AgentSkillFile.Portability.DEVICE_SCOPED,
        False,
        "file is outside portable skill package roots",
    )


def _read_skill_file(path: Path) -> tuple[bytes, str | None]:
    content_bytes = path.read_bytes()
    try:
        return content_bytes, content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return content_bytes, None


def _scan_file(skill_dir: Path, path: Path) -> tuple[SkillFileScan, str]:
    relative_path = normalize_package_path(path.relative_to(skill_dir))
    path_classification = classify_codex_skill_path(relative_path)
    if path_classification is not None:
        return (
            SkillFileScan(
                relative_path=relative_path,
                portability=path_classification.portability,
                included_by_default=path_classification.included_by_default,
                exclusion_reason=path_classification.exclusion_reason,
                size_bytes=0,
                content_sha256=EMPTY_CONTENT_SHA256,
            ),
            "",
        )

    content_bytes, text = _read_skill_file(path)
    classification = classify_codex_skill_file(relative_path, text)
    stored_text = (
        text if classification.included_by_default and text is not None else ""
    )
    stored_bytes = stored_text.encode("utf-8")
    digest = hashlib.sha256(stored_bytes).hexdigest()
    return (
        SkillFileScan(
            relative_path=relative_path,
            portability=classification.portability,
            included_by_default=classification.included_by_default,
            exclusion_reason=classification.exclusion_reason,
            size_bytes=len(stored_bytes),
            content_sha256=digest,
        ),
        stored_text,
    )


def _restore_or_create_skill(*, slug: str, title: str, markdown: str) -> AgentSkill:
    skill = AgentSkill.all_objects.filter(slug=slug).first()
    if skill is None:
        return AgentSkill.objects.create(
            slug=slug,
            title=title,
            markdown=markdown,
            is_seed_data=False,
        )
    was_deleted = skill.is_deleted
    skill.title = title
    skill.markdown = markdown
    skill.is_deleted = False
    skill.save(update_fields=["title", "markdown", "is_deleted"])
    AgentSkill.all_objects.filter(pk=skill.pk).update(is_seed_data=False)
    skill.is_seed_data = False
    if was_deleted:
        skill.node_roles.clear()
    return skill


def _sync_package_files(skill: AgentSkill, file_specs: list[dict]) -> None:
    seen_paths = {file_spec["relative_path"] for file_spec in file_specs}
    skill.package_files.exclude(relative_path__in=seen_paths).delete()
    AgentSkillFile.objects.bulk_create(
        [AgentSkillFile(skill=skill, **file_spec) for file_spec in file_specs],
        update_conflicts=True,
        update_fields=[
            "content",
            "content_sha256",
            "portability",
            "included_by_default",
            "exclusion_reason",
            "size_bytes",
        ],
        unique_fields=["skill", "relative_path"],
    )


def _import_file_spec(file_info: dict, content: str) -> dict:
    classification = classify_codex_skill_file(file_info["path"], content)
    manifest_included = file_info.get("included_by_default", True)
    included_by_default = manifest_included and classification.included_by_default
    stored_content = content if included_by_default else ""
    exclusion_reason = ""
    if not included_by_default:
        exclusion_reason = (
            file_info.get("exclusion_reason")
            or classification.exclusion_reason
            or "excluded by package manifest"
        )
    return {
        "relative_path": file_info["path"],
        "content": stored_content,
        "content_sha256": hashlib.sha256(stored_content.encode("utf-8")).hexdigest(),
        "portability": classification.portability,
        "included_by_default": included_by_default,
        "exclusion_reason": exclusion_reason,
        "size_bytes": len(stored_content.encode("utf-8")),
    }


def _read_package_text(package: ZipFile, archive_path: str) -> str:
    try:
        content = package.read(archive_path)
    except KeyError as error:
        raise ValueError(f"Missing package file: {archive_path}") from error
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Invalid UTF-8 package file: {archive_path}") from error


def _validate_manifest_skill_entries(package: ZipFile, manifest: dict) -> list[dict]:
    validated_skills = []
    seen_slugs = set()
    for skill_entry in manifest.get("skills", []):
        slug = validate_package_skill_slug(skill_entry["slug"])
        if slug in seen_slugs:
            raise ValueError(f"Duplicate package skill slug: {slug}")
        seen_slugs.add(slug)
        seen_paths = set()
        content_by_path = {}
        validated_files = [
            {
                **file_info,
                "path": validate_package_relative_path(file_info["path"]),
            }
            for file_info in skill_entry.get("files", [])
        ]
        for file_info in validated_files:
            if file_info["path"] in seen_paths:
                raise ValueError(f"Duplicate package file path: {file_info['path']}")
            if (
                file_info["path"] == SKILL_MARKDOWN
                and file_info.get("included_by_default") is False
            ):
                raise ValueError(f"{SKILL_MARKDOWN} must be included")
            seen_paths.add(file_info["path"])
            archive_path = f"skills/{slug}/{file_info['path']}"
            content_by_path[file_info["path"]] = _read_package_text(
                package,
                archive_path,
            )
        if SKILL_MARKDOWN not in content_by_path:
            raise ValueError(f"Missing required {SKILL_MARKDOWN} for skill: {slug}")
        validated_skills.append(
            {
                **skill_entry,
                "slug": slug,
                "files": validated_files,
                "content_by_path": content_by_path,
            }
        )
    return validated_skills


def scan_codex_skill_directory(skill_dir: Path, *, dry_run: bool = True) -> dict:
    skill_dir = Path(skill_dir)
    slug = validate_package_skill_slug(skill_dir.name)
    skill_md = skill_dir / SKILL_MARKDOWN
    if not skill_md.exists():
        raise ValueError(f"{skill_dir} does not contain {SKILL_MARKDOWN}")

    file_entries = []
    file_content_by_path = {}
    for path in sorted(skill_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        scan, text = _scan_file(skill_dir, path)
        file_entries.append(scan)
        file_content_by_path[scan.relative_path] = text

    summary = {
        "slug": slug,
        "dry_run": dry_run,
        "files": [asdict(entry) for entry in file_entries],
        "included": sum(1 for entry in file_entries if entry.included_by_default),
        "excluded": sum(1 for entry in file_entries if not entry.included_by_default),
    }
    if dry_run:
        return summary

    with transaction.atomic():
        skill = _restore_or_create_skill(
            slug=slug,
            title=slug.replace("-", " ").title(),
            markdown=file_content_by_path.get(SKILL_MARKDOWN, ""),
        )
        _sync_package_files(
            skill,
            [
                {
                    "relative_path": entry.relative_path,
                    "content": (
                        file_content_by_path[entry.relative_path]
                        if entry.included_by_default
                        else ""
                    ),
                    "content_sha256": entry.content_sha256,
                    "portability": entry.portability,
                    "included_by_default": entry.included_by_default,
                    "exclusion_reason": entry.exclusion_reason,
                    "size_bytes": entry.size_bytes,
                }
                for entry in file_entries
            ],
        )
    return summary


def scan_codex_skills_root(source: Path, *, dry_run: bool = True) -> dict:
    source = Path(source)
    summaries = []
    for child in sorted(source.iterdir()):
        if child.is_dir() and (child / SKILL_MARKDOWN).exists():
            summaries.append(scan_codex_skill_directory(child, dry_run=dry_run))
    return {"source": str(source), "dry_run": dry_run, "skills": summaries}


def export_codex_skill_package(
    output_path: Path,
    *,
    skill_slugs: list[str] | None = None,
    portable_only: bool = True,
) -> dict:
    output_path = Path(output_path)
    queryset = AgentSkill.objects.prefetch_related("package_files")
    if skill_slugs:
        queryset = queryset.filter(slug__in=skill_slugs)

    manifest = {"format": PACKAGE_FORMAT, "skills": []}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_path, "w", ZIP_DEFLATED) as package:
        for skill in queryset.order_by("slug"):
            skill_entry = {
                "slug": skill.slug,
                "title": skill.title,
                "files": [],
            }
            package_files = list(skill.package_files.all())
            if not package_files:
                archive_path = f"skills/{skill.slug}/{SKILL_MARKDOWN}"
                package.writestr(archive_path, skill.markdown)
                skill_entry["files"].append(
                    {
                        "path": SKILL_MARKDOWN,
                        "portability": AgentSkillFile.Portability.PORTABLE,
                        "included_by_default": True,
                        "exclusion_reason": "",
                        "content_sha256": hashlib.sha256(
                            skill.markdown.encode("utf-8")
                        ).hexdigest(),
                    }
                )
            for file_entry in package_files:
                if portable_only and not file_entry.included_by_default:
                    continue
                archive_path = f"skills/{skill.slug}/{file_entry.relative_path}"
                package.writestr(archive_path, file_entry.content)
                skill_entry["files"].append(
                    {
                        "path": file_entry.relative_path,
                        "portability": file_entry.portability,
                        "included_by_default": file_entry.included_by_default,
                        "exclusion_reason": file_entry.exclusion_reason,
                        "content_sha256": file_entry.content_sha256,
                    }
                )
            if skill_entry["files"]:
                manifest["skills"].append(skill_entry)
        package.writestr("manifest.json", json.dumps(manifest, indent=2))
    return manifest


def _included_package_files(skill: AgentSkill) -> list[AgentSkillFile]:
    return [
        file_entry
        for file_entry in skill.package_files.all()
        if file_entry.included_by_default
    ]


def _legacy_skill_file_entry(skill: AgentSkill) -> tuple[str, str]:
    return SKILL_MARKDOWN, skill.markdown


def _package_file_entries(skill: AgentSkill) -> list[tuple[str, str]]:
    package_files = _included_package_files(skill)
    if not package_files:
        return [_legacy_skill_file_entry(skill)]

    entries = [
        (file_entry.relative_path, file_entry.content)
        for file_entry in package_files
        if file_entry.relative_path != SKILL_MARKDOWN
    ]
    entries.insert(0, _legacy_skill_file_entry(skill))
    return entries


def materialize_codex_skill_files(
    target_root: Path,
    *,
    skill_slugs: list[str] | None = None,
    resolve_sigils_on_write: bool = True,
    allowed_roots: set[str] | frozenset[str] | None = DEFAULT_MATERIALIZE_SIGIL_ROOTS,
) -> dict:
    """Write stored portable skill trees to a local skills directory.

    Stored package content remains generic. SIGILS are resolved only while
    writing to the target node, so portable documents can adapt to local suite
    paths and role metadata without copying operator-specific state.
    """

    target_root = Path(target_root)
    queryset = AgentSkill.objects.prefetch_related("package_files")
    if skill_slugs:
        queryset = queryset.filter(slug__in=skill_slugs)

    target_root.mkdir(parents=True, exist_ok=True)
    resolved_target_root = target_root.resolve(strict=False)
    if skill_slugs is None:
        keep_slugs = set(queryset.values_list("slug", flat=True))
        for child in target_root.iterdir():
            if child.is_dir() and child.name not in keep_slugs:
                _remove_tree_best_effort(child)

    summary = {
        "target": str(target_root),
        "resolve_sigils_on_write": resolve_sigils_on_write,
        "skills": [],
        "files_written": 0,
    }
    for skill in queryset.order_by("slug"):
        slug = validate_package_skill_slug(skill.slug)
        skill_dir = target_root / slug
        if skill_dir.is_symlink():
            raise ValueError(f"Unsafe skill directory symlink: {skill_dir}")
        if skill_dir.exists() and not skill_dir.is_dir():
            skill_dir.unlink()
        skill_dir.mkdir(parents=True, exist_ok=True)
        resolved_skill_dir = skill_dir.resolve(strict=False)
        try:
            resolved_skill_dir.relative_to(resolved_target_root)
        except ValueError as error:
            raise ValueError(f"Unsafe skill directory: {skill_dir}") from error
        package_managed = bool(_included_package_files(skill))
        files = []
        entries = []
        for relative_path, content in _package_file_entries(skill):
            package_path = validate_package_relative_path(relative_path)
            entries.append((relative_path, package_path, content))
        desired_paths = {package_path for _, package_path, _ in entries}
        _validate_materialized_package_paths(
            [package_path for _, package_path, _ in entries]
        )

        for relative_path, package_path, content in entries:
            target_path = skill_dir / package_path
            _prepare_materialized_file_path(target_path, skill_dir)
            resolved_target_path = target_path.resolve(strict=False)
            try:
                resolved_target_path.relative_to(resolved_skill_dir)
            except ValueError as error:
                raise ValueError(f"Unsafe package path: {relative_path}") from error
            resolved_content = _resolve_document_sigils(
                content,
                resolve_sigils_on_write=resolve_sigils_on_write,
                allowed_roots=allowed_roots,
            )
            existing = (
                target_path.read_text(encoding="utf-8")
                if target_path.exists()
                else None
            )
            changed = existing != resolved_content
            if changed:
                target_path.write_text(resolved_content, encoding="utf-8")
                summary["files_written"] += 1
            files.append({"path": package_path, "changed": changed})

        if package_managed:
            _prune_stale_materialized_files(skill_dir, desired_paths)

        summary["skills"].append({"slug": slug, "files": files})
    return summary


def import_codex_skill_package(package_path: Path, *, dry_run: bool = True) -> dict:
    package_path = Path(package_path)
    with ZipFile(package_path) as package:
        manifest = json.loads(package.read("manifest.json").decode("utf-8"))
        if manifest.get("format") != PACKAGE_FORMAT:
            raise ValueError("Unsupported Codex skill package format")
        validated_skills = _validate_manifest_skill_entries(package, manifest)
        summary = {
            "package": str(package_path),
            "dry_run": dry_run,
            "skills": [],
        }
        if dry_run:
            for skill_entry in validated_skills:
                summary["skills"].append(
                    {
                        "slug": skill_entry["slug"],
                        "files": len(skill_entry.get("files", [])),
                    }
                )
            return summary

        with transaction.atomic():
            for skill_entry in validated_skills:
                slug = skill_entry["slug"]
                files = skill_entry.get("files", [])
                content_by_path = skill_entry["content_by_path"]
                skill = _restore_or_create_skill(
                    slug=slug,
                    title=skill_entry.get("title", slug.replace("-", " ").title()),
                    markdown=content_by_path.get(SKILL_MARKDOWN, ""),
                )
                _sync_package_files(
                    skill,
                    [
                        _import_file_spec(file_info, content_by_path[file_info["path"]])
                        for file_info in files
                    ],
                )
                summary["skills"].append({"slug": slug, "files": len(files)})
        return summary
