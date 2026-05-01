from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.db import transaction

from apps.skills.models import AgentSkill, AgentSkillFile

PACKAGE_FORMAT = "arthexis.codex_skill_package.v1"
SKILL_MARKDOWN = "SKILL.md"
EMPTY_CONTENT_SHA256 = hashlib.sha256(b"").hexdigest()

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
    if (
        not normalized
        or normalized == "."
        or path.is_absolute()
        or Path(relative_path).is_absolute()
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
