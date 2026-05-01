from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from django.db import transaction

from apps.skills.models import AgentSkill, AgentSkillFile

PACKAGE_FORMAT = "arthexis.codex_skill_package.v1"

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


def classify_codex_skill_file(
    relative_path: str,
    content: str | None,
) -> SkillFileClassification:
    parts = [part.lower() for part in relative_path.replace("\\", "/").split("/")]
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
    if content is None:
        return SkillFileClassification(
            AgentSkillFile.Portability.DEVICE_SCOPED,
            False,
            "binary files are not exported by this prototype",
        )
    if any(marker in content for marker in OPERATOR_PATH_MARKERS):
        return SkillFileClassification(
            AgentSkillFile.Portability.OPERATOR_SCOPED,
            False,
            "operator-local paths must be parameterized before export",
        )
    return SkillFileClassification(AgentSkillFile.Portability.PORTABLE, True)


def _read_skill_file(path: Path) -> tuple[bytes, str | None]:
    content_bytes = path.read_bytes()
    try:
        return content_bytes, content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return content_bytes, None


def _scan_file(skill_dir: Path, path: Path) -> tuple[SkillFileScan, str]:
    relative_path = normalize_package_path(path.relative_to(skill_dir))
    content_bytes, text = _read_skill_file(path)
    classification = classify_codex_skill_file(relative_path, text)
    digest = hashlib.sha256(content_bytes).hexdigest()
    return (
        SkillFileScan(
            relative_path=relative_path,
            portability=classification.portability,
            included_by_default=classification.included_by_default,
            exclusion_reason=classification.exclusion_reason,
            size_bytes=len(content_bytes),
            content_sha256=digest,
        ),
        text or "",
    )


def _restore_or_create_skill(*, slug: str, title: str, markdown: str) -> AgentSkill:
    skill = AgentSkill.all_objects.filter(slug=slug).first()
    if skill is None:
        return AgentSkill.objects.create(slug=slug, title=title, markdown=markdown)
    skill.title = title
    skill.markdown = markdown
    skill.is_deleted = False
    skill.save(update_fields=["title", "markdown", "is_deleted"])
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


def scan_codex_skill_directory(skill_dir: Path, *, dry_run: bool = True) -> dict:
    skill_dir = Path(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise ValueError(f"{skill_dir} does not contain SKILL.md")

    file_entries = []
    file_content_by_path = {}
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        scan, text = _scan_file(skill_dir, path)
        file_entries.append(scan)
        file_content_by_path[scan.relative_path] = text

    summary = {
        "slug": skill_dir.name,
        "dry_run": dry_run,
        "files": [asdict(entry) for entry in file_entries],
        "included": sum(1 for entry in file_entries if entry.included_by_default),
        "excluded": sum(1 for entry in file_entries if not entry.included_by_default),
    }
    if dry_run:
        return summary

    with transaction.atomic():
        skill = _restore_or_create_skill(
            slug=skill_dir.name,
            title=skill_dir.name.replace("-", " ").title(),
            markdown=file_content_by_path.get("SKILL.md", ""),
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
        if child.is_dir() and (child / "SKILL.md").exists():
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
            for file_entry in skill.package_files.all():
                if portable_only and not file_entry.included_by_default:
                    continue
                archive_path = f"skills/{skill.slug}/{file_entry.relative_path}"
                package.writestr(archive_path, file_entry.content)
                skill_entry["files"].append(
                    {
                        "path": file_entry.relative_path,
                        "portability": file_entry.portability,
                        "included_by_default": file_entry.included_by_default,
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
        summary = {
            "package": str(package_path),
            "dry_run": dry_run,
            "skills": [],
        }
        if dry_run:
            for skill_entry in manifest.get("skills", []):
                summary["skills"].append(
                    {
                        "slug": skill_entry["slug"],
                        "files": len(skill_entry.get("files", [])),
                    }
                )
            return summary

        with transaction.atomic():
            for skill_entry in manifest.get("skills", []):
                slug = skill_entry["slug"]
                files = skill_entry.get("files", [])
                content_by_path = {
                    file_info["path"]: package.read(
                        f"skills/{slug}/{file_info['path']}"
                    ).decode("utf-8")
                    for file_info in files
                }
                skill = _restore_or_create_skill(
                    slug=slug,
                    title=skill_entry.get("title", slug.replace("-", " ").title()),
                    markdown=content_by_path.get("SKILL.md", ""),
                )
                _sync_package_files(
                    skill,
                    [
                        {
                            "relative_path": file_info["path"],
                            "content": content,
                            "content_sha256": hashlib.sha256(
                                content.encode("utf-8")
                            ).hexdigest(),
                            "portability": file_info.get(
                                "portability", AgentSkillFile.Portability.PORTABLE
                            ),
                            "included_by_default": file_info.get(
                                "included_by_default", True
                            ),
                            "exclusion_reason": "",
                            "size_bytes": len(content.encode("utf-8")),
                        }
                        for file_info in files
                        for content in [content_by_path[file_info["path"]]]
                    ],
                )
                summary["skills"].append({"slug": slug, "files": len(files)})
        return summary
