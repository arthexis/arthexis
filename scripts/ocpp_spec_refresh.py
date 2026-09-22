"""Refresh OCPP action-surface evidence from official OCA packages.

This tool is intentionally stdlib-only so the manual GitHub-hosted refresh job
can run without adding application dependencies. It downloads the current OCA
"all files" archives, hashes them, extracts request action names from JSON
schemas, and compares the package surface with the committed reviewed manifests.

It never rewrites the committed manifests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
import zipfile
from collections.abc import Iterator
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

DEFAULT_SOURCE_PAGE = "https://openchargealliance.org/my-oca/ocpp/"
USER_AGENT = "Arthexis-OCPP-Spec-Refresh/1.0"


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._href = dict(attrs).get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            self.links.append((text, self._href))
            self._href = None
            self._text = []


def discover_download_url(html: str, *, label: str, base_url: str) -> str:
    """Return the exact OCA download link whose visible label matches."""
    parser = _LinkParser()
    parser.feed(html)
    matches = [href for text, href in parser.links if text == label]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one OCA download link for {label!r}; found {len(matches)}"
        )
    return urljoin(base_url, matches[0])


def iter_archive_json(
    archive: bytes,
    *,
    archive_name: str = "package.zip",
    max_depth: int = 4,
) -> Iterator[tuple[str, bytes]]:
    """Yield JSON files from a ZIP and any nested ZIP bundles.

    OCA "all files" downloads may wrap the machine-readable schema archive inside
    another ZIP. Only JSON and ZIP members are read so PDFs and other publication
    assets do not need to be loaded into memory.
    """
    yield from _iter_archive_json(
        archive,
        archive_name=archive_name,
        depth=0,
        max_depth=max_depth,
    )


def _iter_archive_json(
    archive: bytes,
    *,
    archive_name: str,
    depth: int,
    max_depth: int,
) -> Iterator[tuple[str, bytes]]:
    if depth > max_depth:
        raise RuntimeError(
            f"OCPP package nesting exceeds {max_depth} levels at {archive_name}"
        )

    try:
        bundle = zipfile.ZipFile(BytesIO(archive))
    except zipfile.BadZipFile as error:
        raise RuntimeError(f"Invalid ZIP archive: {archive_name}") from error

    with bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue

            lower_name = member.filename.lower()
            virtual_path = f"{archive_name}!/{member.filename}"

            if lower_name.endswith(".json"):
                yield virtual_path, bundle.read(member)
                continue

            if lower_name.endswith(".zip"):
                nested = bundle.read(member)
                yield from _iter_archive_json(
                    nested,
                    archive_name=virtual_path,
                    depth=depth + 1,
                    max_depth=max_depth,
                )


def identify_schema(path: str, payload: object) -> tuple[str, str] | None:
    """Identify an OCPP action schema by JSON title, then filename fallback."""
    if isinstance(payload, dict):
        title = payload.get("title")
        if isinstance(title, str):
            for suffix, kind in (("Request", "request"), ("Response", "response")):
                if title.endswith(suffix):
                    action = title[: -len(suffix)]
                    if action:
                        return action, kind

    filename = Path(path.split("!/")[-1]).stem
    for suffix, kind in (("Request", "request"), ("Response", "response")):
        match = re.fullmatch(rf"(.+){suffix}", filename)
        if match:
            return match.group(1), kind
    return None


def extract_request_actions(archive: bytes) -> set[str]:
    """Extract action names from JSON schemas at any ZIP nesting depth."""
    actions: set[str] = set()
    for path, raw_payload in iter_archive_json(archive):
        try:
            payload = json.loads(raw_payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        identified = identify_schema(path, payload)
        if identified is not None:
            action, kind = identified
            if kind == "request":
                actions.add(action)

    if not actions:
        raise RuntimeError("No OCPP request schemas found in downloaded package")
    return actions


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _load_manifest(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def refresh_manifest(path: Path, *, output_dir: Path) -> tuple[bool, dict[str, object]]:
    manifest = _load_manifest(path)
    source_page = str(manifest.get("source_page") or DEFAULT_SOURCE_PAGE)
    label = str(manifest["download_label"])

    listing = _get(source_page).decode("utf-8")
    download_url = discover_download_url(listing, label=label, base_url=source_page)
    archive = _get(download_url)
    digest = hashlib.sha256(archive).hexdigest()
    package_actions = extract_request_actions(archive)

    directions = manifest["directions"]
    reviewed_actions = set(directions["charge_point_to_csms"]) | set(
        directions["csms_to_charge_point"]
    )
    added = sorted(package_actions - reviewed_actions)
    removed = sorted(reviewed_actions - package_actions)
    matches = not added and not removed

    result = {
        "protocol": manifest["protocol"],
        "publication": manifest["publication"],
        "download_label": label,
        "download_url": download_url,
        "archive_sha256": digest,
        "archive_bytes": len(archive),
        "request_action_count": len(package_actions),
        "matches_reviewed_surface": matches,
        "added_actions": added,
        "removed_actions": removed,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{path.stem}.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return matches, result


def _write_report(results: list[dict[str, object]], output_dir: Path) -> None:
    lines = ["# OCPP official specification refresh", ""]
    for result in results:
        lines.extend(
            [
                f"## {result['protocol']}",
                "",
                f"- Publication: {result['publication']}",
                f"- SHA-256: `{result['archive_sha256']}`",
                f"- Archive bytes: {result['archive_bytes']}",
                f"- Request actions: {result['request_action_count']}",
                (
                    "- Reviewed surface: **match**"
                    if result["matches_reviewed_surface"]
                    else "- Reviewed surface: **changed**"
                ),
            ]
        )
        if result["added_actions"]:
            lines.append(
                "- Added upstream actions: "
                + ", ".join(f"`{value}`" for value in result["added_actions"])
            )
        if result["removed_actions"]:
            lines.append(
                "- Removed upstream actions: "
                + ", ".join(f"`{value}`" for value in result["removed_actions"])
            )
        lines.append("")
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=Path("tests/ocpp/spec"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    paths = [
        args.manifest_dir / "ocpp16.json",
        args.manifest_dir / "ocpp201.json",
    ]
    results: list[dict[str, object]] = []
    all_match = True
    for path in paths:
        matches, result = refresh_manifest(path, output_dir=args.output_dir)
        results.append(result)
        all_match = all_match and matches

    _write_report(results, args.output_dir)
    print(
        "official_surface=unchanged" if all_match else "official_surface=changed"
    )
    return 0 if all_match else 2


if __name__ == "__main__":
    raise SystemExit(main())
