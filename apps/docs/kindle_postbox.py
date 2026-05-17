from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import DatabaseError

from apps.docs import rendering
from apps.nodes.roles import node_is_control
from apps.sensors import usb_inventory

KINDLE_POSTBOX_NODE_FEATURE_SLUG = "kindle-postbox"
KINDLE_POSTBOX_USB_CLAIM = "kindle-postbox"
KINDLE_POSTBOX_SUITE_BUNDLE = "suite"
KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE = "operators"
KINDLE_POSTBOX_BUNDLE_CHOICES = (
    KINDLE_POSTBOX_SUITE_BUNDLE,
    KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE,
)
KINDLE_POSTBOX_BUNDLE_FILENAME = "arthexis-suite-documentation.txt"
KINDLE_POSTBOX_MANIFEST_FILENAME = "arthexis-suite-documentation.json"
KINDLE_OPERATORS_MANUAL_FILENAME = "arthexis-operators-manual.txt"
KINDLE_OPERATORS_MANUAL_MANIFEST_FILENAME = "arthexis-operators-manual.json"
KINDLE_OPERATORS_MANUAL_SOURCE_MANIFEST = Path("docs/operators-manual.json")
KINDLE_DOCUMENTS_DIR_NAME = "documents"
GENERATED_DOCUMENTATION_FILENAMES = frozenset(
    {
        KINDLE_POSTBOX_BUNDLE_FILENAME,
        KINDLE_POSTBOX_MANIFEST_FILENAME,
        KINDLE_OPERATORS_MANUAL_FILENAME,
        KINDLE_OPERATORS_MANUAL_MANIFEST_FILENAME,
    }
)
DOCUMENT_EXTENSIONS = (
    rendering.MARKDOWN_FILE_EXTENSIONS
    | rendering.PLAINTEXT_FILE_EXTENSIONS
    | rendering.CSV_FILE_EXTENSIONS
    | {".rst"}
)


class DocumentationBundleError(Exception):
    """Raised when a Kindle documentation bundle cannot be generated."""


@dataclass(frozen=True)
class OperatorManualSection:
    title: str
    sources: tuple[Path, ...]


@dataclass(frozen=True)
class DocumentationBundle:
    output_path: Path
    manifest_path: Path
    generated_at: str
    document_count: int
    byte_count: int
    sources: tuple[str, ...]
    title: str = "Arthexis Suite Documentation"
    bundle: str = KINDLE_POSTBOX_SUITE_BUNDLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "manifest_path": str(self.manifest_path),
            "generated_at": self.generated_at,
            "document_count": self.document_count,
            "byte_count": self.byte_count,
            "sources": list(self.sources),
            "title": self.title,
            "bundle": self.bundle,
        }


@dataclass(frozen=True)
class KindlePostboxPublishResult:
    public_library: Path
    output_path: Path
    status: str
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "public_library": str(self.public_library),
            "output_path": str(self.output_path),
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class KindlePostboxTargetResult:
    root_path: Path
    documents_path: Path | None
    output_path: Path | None
    status: str
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "root_path": str(self.root_path),
            "documents_path": str(self.documents_path) if self.documents_path else "",
            "output_path": str(self.output_path) if self.output_path else "",
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class KindlePostboxSyncResult:
    bundle: DocumentationBundle
    targets: tuple[KindlePostboxTargetResult, ...]
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "bundle": self.bundle.as_dict(),
            "targets": [target.as_dict() for target in self.targets],
            "dry_run": self.dry_run,
        }


def kindle_postbox_available(*, node: object) -> bool:
    """Return whether this node can run Kindle postbox sync."""

    return bool(node is not None and node_is_control(node))


def default_output_dir() -> Path:
    return Path(
        getattr(
            settings,
            "KINDLE_POSTBOX_OUTPUT_DIR",
            Path(settings.BASE_DIR) / "work" / "docs" / "kindle-postbox",
        )
    )


def _normalize_bundle(bundle: str) -> str:
    normalized = str(bundle or KINDLE_POSTBOX_SUITE_BUNDLE).strip().casefold()
    if normalized not in KINDLE_POSTBOX_BUNDLE_CHOICES:
        raise DocumentationBundleError(
            f"Unsupported Kindle documentation bundle: {bundle}"
        )
    return normalized


def _bundle_title(bundle: str) -> str:
    if bundle == KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE:
        return "Arthexis Operators Manual"
    return "Arthexis Suite Documentation"


def _bundle_filename(bundle: str) -> str:
    if bundle == KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE:
        return KINDLE_OPERATORS_MANUAL_FILENAME
    return KINDLE_POSTBOX_BUNDLE_FILENAME


def _bundle_manifest_filename(bundle: str) -> str:
    if bundle == KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE:
        return KINDLE_OPERATORS_MANUAL_MANIFEST_FILENAME
    return KINDLE_POSTBOX_MANIFEST_FILENAME


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _path_is_excluded(path: Path, excluded_roots: tuple[Path, ...]) -> bool:
    if path.name in GENERATED_DOCUMENTATION_FILENAMES:
        return True
    return any(
        path == excluded_root or _path_is_relative_to(path, excluded_root)
        for excluded_root in excluded_roots
    )


def _resolve_document_candidate(
    path: Path,
    *,
    root: Path,
    excluded_roots: tuple[Path, ...],
    seen: set[Path],
) -> Path | None:
    if path.suffix.lower() not in DOCUMENT_EXTENSIONS:
        return None
    resolved = path.resolve()
    if resolved in seen or not _path_is_relative_to(resolved, root):
        return None
    if _path_is_excluded(resolved, excluded_roots):
        return None
    return resolved


def _iter_docs_root_files(docs_root: Path) -> Iterable[Path]:
    if not docs_root.exists():
        return
    for path in sorted(docs_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(docs_root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.suffix.lower() in DOCUMENT_EXTENSIONS:
            yield path


def iter_suite_documentation_files(
    *,
    base_dir: Path | None = None,
    exclude_paths: Iterable[Path] | None = None,
) -> list[Path]:
    """Return documentation files that form the suite documentation bundle."""

    root = Path(base_dir or settings.BASE_DIR).resolve()
    excluded_roots = tuple(Path(path).resolve() for path in exclude_paths or ())
    seen: set[Path] = set()
    documents: list[Path] = []

    def add_candidate(path: Path) -> None:
        resolved = _resolve_document_candidate(
            path,
            root=root,
            excluded_roots=excluded_roots,
            seen=seen,
        )
        if resolved is None:
            return
        seen.add(resolved)
        documents.append(resolved)

    readme = root / "README.md"
    if readme.is_file():
        add_candidate(readme)

    for docs_root in (root / "docs", root / "apps" / "docs"):
        for path in _iter_docs_root_files(docs_root):
            add_candidate(path)
    return documents


def _write_suite_documentation_payload(
    *,
    output_path: Path,
    root: Path,
    generated_at: str,
    documents: list[Path],
    sources: tuple[str, ...],
    title: str = "Arthexis Suite Documentation",
) -> None:
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{title}\n")
        handle.write(f"Generated: {generated_at}\n")
        handle.write(f"Source root: {root}\n")
        handle.write(f"Documents: {len(documents)}\n")
        handle.write("\n")
        handle.write("Contents\n")
        handle.write("========\n")
        for index, source in enumerate(sources, start=1):
            handle.write(f"{index}. {source}\n")
        if documents:
            handle.write("\n")

        last_index = len(documents) - 1
        for index, (source, path) in enumerate(zip(sources, documents, strict=True)):
            handle.write("=" * 78)
            handle.write("\n")
            handle.write(source)
            handle.write("\n")
            handle.write("=" * 78)
            handle.write("\n\n")
            handle.write(_read_document_text_for_bundle(path).strip())
            handle.write("\n")
            if index != last_index:
                handle.write("\n")


def _manifest_error(message: str, manifest_path: Path) -> DocumentationBundleError:
    return DocumentationBundleError(f"{manifest_path}: {message}")


def _load_json_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _manifest_error(str(exc), manifest_path) from exc
    except json.JSONDecodeError as exc:
        raise _manifest_error(f"invalid JSON: {exc}", manifest_path) from exc
    if not isinstance(payload, dict):
        raise _manifest_error("manifest must be a JSON object", manifest_path)
    return payload


def _relative_manifest_path(source: object, manifest_path: Path) -> Path:
    text = str(source or "").strip()
    if not text:
        raise _manifest_error("source path cannot be empty", manifest_path)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise _manifest_error(
            f"source path must stay inside the repo: {text}",
            manifest_path,
        )
    return path


def _resolve_operator_manual_source(
    source: object,
    *,
    root: Path,
    manifest_path: Path,
    excluded_roots: tuple[Path, ...],
    seen: set[Path],
) -> Path | None:
    relative = _relative_manifest_path(source, manifest_path)
    candidate = (root / relative).resolve()
    if not candidate.is_file():
        raise _manifest_error(
            f"source file is missing: {relative.as_posix()}",
            manifest_path,
        )
    resolved = _resolve_document_candidate(
        candidate,
        root=root,
        excluded_roots=excluded_roots,
        seen=seen,
    )
    if resolved is None:
        return None
    seen.add(resolved)
    return resolved


def iter_operator_manual_sections(
    *,
    base_dir: Path | None = None,
    manifest_path: Path | None = None,
    exclude_paths: Iterable[Path] | None = None,
) -> list[OperatorManualSection]:
    """Return the curated operator-manual sections from the source manifest."""

    root = Path(base_dir or settings.BASE_DIR).resolve()
    source_manifest = (
        Path(manifest_path)
        if manifest_path is not None
        else root / KINDLE_OPERATORS_MANUAL_SOURCE_MANIFEST
    )
    if not source_manifest.is_absolute():
        source_manifest = root / source_manifest
    source_manifest = source_manifest.resolve()
    payload = _load_json_manifest(source_manifest)
    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise _manifest_error(
            "manifest requires at least one section",
            source_manifest,
        )

    excluded_roots = tuple(Path(path).resolve() for path in exclude_paths or ())
    seen: set[Path] = set()
    sections: list[OperatorManualSection] = []
    for index, raw_section in enumerate(raw_sections, start=1):
        if not isinstance(raw_section, dict):
            raise _manifest_error(
                f"section {index} must be an object",
                source_manifest,
            )
        title = str(raw_section.get("title") or "").strip()
        if not title:
            raise _manifest_error(f"section {index} requires a title", source_manifest)
        raw_sources = raw_section.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise _manifest_error(f"section {index} requires sources", source_manifest)
        sources = tuple(
            source
            for source in (
                _resolve_operator_manual_source(
                    raw_source,
                    root=root,
                    manifest_path=source_manifest,
                    excluded_roots=excluded_roots,
                    seen=seen,
                )
                for raw_source in raw_sources
            )
            if source is not None
        )
        if sources:
            sections.append(OperatorManualSection(title=title, sources=sources))
    if not sections:
        raise _manifest_error(
            "manifest did not resolve any documentation sources",
            source_manifest,
        )
    return sections


def _write_operator_manual_payload(
    *,
    output_path: Path,
    root: Path,
    generated_at: str,
    sections: list[OperatorManualSection],
    sources: tuple[str, ...],
) -> None:
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("Arthexis Operators Manual\n")
        handle.write(f"Generated: {generated_at}\n")
        handle.write(f"Source root: {root}\n")
        handle.write(f"Documents: {len(sources)}\n")
        handle.write("\n")
        handle.write("Contents\n")
        handle.write("========\n")
        source_index = 1
        for section_index, section in enumerate(sections, start=1):
            handle.write(f"{section_index}. {section.title}\n")
            for source in section.sources:
                relative = source.relative_to(root).as_posix()
                handle.write(f"   {source_index}. {relative}\n")
                source_index += 1
        handle.write("\n")

        for section_index, section in enumerate(sections, start=1):
            if section_index > 1:
                handle.write("\n")
            handle.write("=" * 78)
            handle.write("\n")
            handle.write(section.title)
            handle.write("\n")
            handle.write("=" * 78)
            handle.write("\n\n")
            last_index = len(section.sources) - 1
            for index, path in enumerate(section.sources):
                source = path.relative_to(root).as_posix()
                handle.write(source)
                handle.write("\n")
                handle.write("-" * len(source))
                handle.write("\n\n")
                handle.write(_read_document_text_for_bundle(path).strip())
                handle.write("\n")
                if index != last_index:
                    handle.write("\n")


def _read_document_text_for_bundle(path: Path) -> str:
    try:
        return rendering.read_document_text(path)
    except DatabaseError:
        return path.read_text(encoding="utf-8", errors="replace")
    except RuntimeError as exc:
        if "Database access not allowed" not in str(exc):
            raise
        return path.read_text(encoding="utf-8", errors="replace")


def build_suite_documentation_bundle(
    *,
    output_dir: Path | None = None,
    base_dir: Path | None = None,
) -> DocumentationBundle:
    """Generate a single plain-text suite documentation bundle for Kindle readers."""

    root = Path(base_dir or settings.BASE_DIR).resolve()
    destination_dir = Path(output_dir or default_output_dir())
    destination_dir.mkdir(parents=True, exist_ok=True)
    output_path = destination_dir / _bundle_filename(KINDLE_POSTBOX_SUITE_BUNDLE)
    manifest_path = destination_dir / _bundle_manifest_filename(
        KINDLE_POSTBOX_SUITE_BUNDLE
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    documents = iter_suite_documentation_files(base_dir=root)
    sources = tuple(path.relative_to(root).as_posix() for path in documents)

    _write_suite_documentation_payload(
        output_path=output_path,
        root=root,
        generated_at=generated_at,
        documents=documents,
        sources=sources,
    )
    manifest = {
        "bundle": KINDLE_POSTBOX_SUITE_BUNDLE,
        "title": _bundle_title(KINDLE_POSTBOX_SUITE_BUNDLE),
        "generated_at": generated_at,
        "document_count": len(documents),
        "byte_count": output_path.stat().st_size,
        "sources": list(sources),
        "output_path": str(output_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return DocumentationBundle(
        output_path=output_path,
        manifest_path=manifest_path,
        generated_at=generated_at,
        document_count=len(documents),
        byte_count=output_path.stat().st_size,
        sources=sources,
        title=_bundle_title(KINDLE_POSTBOX_SUITE_BUNDLE),
        bundle=KINDLE_POSTBOX_SUITE_BUNDLE,
    )


def build_operators_manual_bundle(
    *,
    output_dir: Path | None = None,
    base_dir: Path | None = None,
    manifest_path: Path | None = None,
) -> DocumentationBundle:
    """Generate the curated plain-text operators manual for Kindle readers."""

    root = Path(base_dir or settings.BASE_DIR).resolve()
    destination_dir = Path(output_dir or default_output_dir())
    destination_dir.mkdir(parents=True, exist_ok=True)
    output_path = destination_dir / _bundle_filename(
        KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE
    )
    manifest_output_path = destination_dir / _bundle_manifest_filename(
        KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    sections = iter_operator_manual_sections(
        base_dir=root,
        manifest_path=manifest_path,
    )
    sources = tuple(
        source.relative_to(root).as_posix()
        for section in sections
        for source in section.sources
    )

    _write_operator_manual_payload(
        output_path=output_path,
        root=root,
        generated_at=generated_at,
        sections=sections,
        sources=sources,
    )
    manifest = {
        "bundle": KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE,
        "title": _bundle_title(KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE),
        "generated_at": generated_at,
        "document_count": len(sources),
        "byte_count": output_path.stat().st_size,
        "sections": [
            {
                "title": section.title,
                "sources": [
                    source.relative_to(root).as_posix()
                    for source in section.sources
                ],
            }
            for section in sections
        ],
        "sources": list(sources),
        "output_path": str(output_path),
    }
    manifest_output_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return DocumentationBundle(
        output_path=output_path,
        manifest_path=manifest_output_path,
        generated_at=generated_at,
        document_count=len(sources),
        byte_count=output_path.stat().st_size,
        sources=sources,
        title=_bundle_title(KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE),
        bundle=KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE,
    )


def build_documentation_bundle(
    *,
    bundle: str = KINDLE_POSTBOX_SUITE_BUNDLE,
    output_dir: Path | None = None,
    base_dir: Path | None = None,
) -> DocumentationBundle:
    """Generate a Kindle documentation bundle by bundle type."""

    normalized = _normalize_bundle(bundle)
    if normalized == KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE:
        return build_operators_manual_bundle(output_dir=output_dir, base_dir=base_dir)
    return build_suite_documentation_bundle(output_dir=output_dir, base_dir=base_dir)


def _resolve_documents_dir(root_path: Path) -> Path | None:
    if not root_path.is_dir():
        return None
    try:
        root_real_path = root_path.resolve()
    except OSError:
        return None
    documents_dir = root_path / KINDLE_DOCUMENTS_DIR_NAME
    if documents_dir.exists():
        if documents_dir.is_symlink() or not documents_dir.is_dir():
            return None
        try:
            resolved_docs = documents_dir.resolve()
        except OSError:
            return None
        if not _path_is_relative_to(resolved_docs, root_real_path):
            return None
        return documents_dir
    return root_path


def _same_file_content(source: Path, destination: Path) -> bool:
    try:
        return destination.is_file() and source.read_bytes() == destination.read_bytes()
    except OSError:
        return False


def _copy_bundle_file(
    source: Path,
    destination: Path,
    *,
    dry_run: bool,
    copied_status: str,
    dry_run_status: str,
) -> tuple[str, str]:
    if _same_file_content(source, destination):
        return "current", ""
    if dry_run:
        return dry_run_status, ""

    temp_path: Path | None = None
    error = ""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)
            with source.open("rb") as source_file:
                shutil.copyfileobj(source_file, temp_file)
        shutil.copystat(source, temp_path, follow_symlinks=False)
        temp_path.replace(destination)
        temp_path = None
    except OSError as exc:
        error = str(exc)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
    if error:
        return "failed", error
    return copied_status, ""


def _copy_bundle_to_target(
    bundle: DocumentationBundle,
    root_path: Path,
    *,
    dry_run: bool,
) -> KindlePostboxTargetResult:
    documents_dir = _resolve_documents_dir(root_path)
    if documents_dir is None:
        return KindlePostboxTargetResult(
            root_path=root_path,
            documents_path=None,
            output_path=None,
            status="missing",
            error="Target path is not a directory.",
        )

    output_path = documents_dir / bundle.output_path.name
    status, error = _copy_bundle_file(
        bundle.output_path,
        output_path,
        dry_run=dry_run,
        copied_status="copied",
        dry_run_status="would-copy",
    )
    if error:
        return KindlePostboxTargetResult(
            root_path=root_path,
            documents_path=documents_dir,
            output_path=output_path,
            status=status,
            error=error,
        )
    return KindlePostboxTargetResult(
        root_path=root_path,
        documents_path=documents_dir,
        output_path=output_path,
        status=status,
    )


def publish_bundle_to_public_library(
    bundle: DocumentationBundle,
    public_library: Path,
    *,
    dry_run: bool = False,
) -> KindlePostboxPublishResult:
    """Copy a generated bundle into a local public library watched by postbox tools."""

    public_library = Path(public_library)
    output_path = public_library / bundle.output_path.name
    status, error = _copy_bundle_file(
        bundle.output_path,
        output_path,
        dry_run=dry_run,
        copied_status="published",
        dry_run_status="would-publish",
    )
    return KindlePostboxPublishResult(
        public_library=public_library,
        output_path=output_path,
        status=status,
        error=error,
    )


def sync_to_kindle_postboxes(
    *,
    bundle: str = KINDLE_POSTBOX_SUITE_BUNDLE,
    output_dir: Path | None = None,
    base_dir: Path | None = None,
    refresh_usb: bool = False,
    dry_run: bool = False,
    targets: list[str | Path] | None = None,
) -> KindlePostboxSyncResult:
    """Build suite documentation and copy it to Kindle postbox targets."""

    built_bundle = build_documentation_bundle(
        bundle=bundle,
        output_dir=output_dir,
        base_dir=base_dir,
    )
    raw_targets = (
        list(targets)
        if targets is not None
        else usb_inventory.claimed_paths(
            KINDLE_POSTBOX_USB_CLAIM,
            refresh=refresh_usb,
        )
    )
    target_results = tuple(
        _copy_bundle_to_target(built_bundle, Path(path), dry_run=dry_run)
        for path in sorted({str(path) for path in raw_targets if str(path).strip()})
    )
    return KindlePostboxSyncResult(
        bundle=built_bundle,
        targets=target_results,
        dry_run=dry_run,
    )


__all__ = [
    "DOCUMENT_EXTENSIONS",
    "GENERATED_DOCUMENTATION_FILENAMES",
    "KINDLE_DOCUMENTS_DIR_NAME",
    "KINDLE_OPERATORS_MANUAL_FILENAME",
    "KINDLE_OPERATORS_MANUAL_MANIFEST_FILENAME",
    "KINDLE_OPERATORS_MANUAL_SOURCE_MANIFEST",
    "KINDLE_POSTBOX_BUNDLE_CHOICES",
    "KINDLE_POSTBOX_BUNDLE_FILENAME",
    "KINDLE_POSTBOX_MANIFEST_FILENAME",
    "KINDLE_POSTBOX_NODE_FEATURE_SLUG",
    "KINDLE_POSTBOX_OPERATORS_MANUAL_BUNDLE",
    "KINDLE_POSTBOX_SUITE_BUNDLE",
    "KINDLE_POSTBOX_USB_CLAIM",
    "DocumentationBundleError",
    "DocumentationBundle",
    "KindlePostboxPublishResult",
    "KindlePostboxSyncResult",
    "KindlePostboxTargetResult",
    "OperatorManualSection",
    "build_documentation_bundle",
    "build_operators_manual_bundle",
    "build_suite_documentation_bundle",
    "default_output_dir",
    "iter_operator_manual_sections",
    "iter_suite_documentation_files",
    "kindle_postbox_available",
    "publish_bundle_to_public_library",
    "sync_to_kindle_postboxes",
]
