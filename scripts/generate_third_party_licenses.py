import json
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
DOC_PATH = ROOT / "docs" / "legal" / "THIRD_PARTY_LICENSES.md"
LGPL3_URL = "https://www.gnu.org/licenses/lgpl-3.0.html"
LICENSE_OVERRIDES: dict[str, tuple[str, str]] = {
    "psycopg": ("LGPL-3.0-or-later", LGPL3_URL),
    "psycopg-binary": ("LGPL-3.0-or-later", LGPL3_URL),
}


def load_dependencies() -> list[tuple[str, str]]:
    data = tomllib.loads(PYPROJECT.read_text())
    deps: list[str] = data.get("project", {}).get("dependencies", [])
    parsed: list[tuple[str, str]] = []
    for dep in deps:
        dep = dep.strip()
        if not dep:
            continue
        marker_split = dep.split(";", 1)
        spec = marker_split[0].strip()
        marker = marker_split[1].strip() if len(marker_split) == 2 else ""
        match = re.match(r"([A-Za-z0-9_.-]+)", spec)
        if not match:
            continue
        name = match.group(1)
        spec_display = spec + (f"; {marker}" if marker else "")
        parsed.append((name, spec_display))
    return parsed


def resolve_dependency_tree(dependencies: list[str]) -> list[tuple[str, str]]:
    if not dependencies:
        return []

    req_path = ""
    try:
        with NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write("\n".join(dependencies))
            req_path = tmp.name

        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--report",
            "-",
            "--ignore-installed",
            "--quiet",
            "-r",
            req_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        if req_path:
            Path(req_path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to resolve dependency tree: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Failed to parse dependency resolution report") from exc

    resolved: list[tuple[str, str]] = []
    for item in report.get("install", []):
        metadata = item.get("metadata") or {}
        name = metadata.get("name")
        version = metadata.get("version")
        if not name or not version:
            continue
        resolved.append((name, f"{name}=={version}"))

    return resolved


def fetch_license(name: str) -> tuple[str, str]:
    url = f"https://pypi.org/pypi/{name}/json"
    try:
        with urlopen(url) as resp:  # noqa: S310 - trusted PyPI host
            payload = json.load(resp)
    except (HTTPError, URLError, json.JSONDecodeError):
        return "Unknown", f"https://pypi.org/project/{name}/"

    info = payload.get("info", {})
    license_name = " ".join((info.get("license") or "").split())
    if not license_name:
        classifiers = [
            c.split("::")[-1].strip()
            for c in info.get("classifiers", [])
            if c.startswith("License ::")
        ]
        if classifiers:
            license_name = "; ".join(classifiers)
        else:
            license_name = "Unknown"
    if not license_name:
        license_name = "Unknown"

    project_urls = info.get("project_urls") or {}
    license_url = (
        project_urls.get("License")
        or project_urls.get("Homepage")
        or f"https://pypi.org/project/{name}/"
    )
    if len(license_name) > 180:
        license_name = license_name[:177] + "..."

    override = LICENSE_OVERRIDES.get(name.lower())
    if override:
        license_name, license_url_override = override
        license_url = license_url_override or license_url

    license_name = license_name.replace("http://", "https://")
    license_url = license_url.replace("http://", "https://")

    return license_name, license_url


def build_inventory() -> list[dict[str, str]]:
    inventory: list[dict[str, str]] = []
    seen: set[str] = set()
    dependency_specs = [spec for _, spec in load_dependencies()]
    for name, spec in resolve_dependency_tree(dependency_specs):
        canonical = name.lower()
        if canonical in seen:
            continue
        seen.add(canonical)
        license_name, license_url = fetch_license(name)
        inventory.append(
            {
                "name": name,
                "spec": spec,
                "license": license_name,
                "license_url": license_url,
            }
        )
    return sorted(inventory, key=lambda item: item["name"].lower())


def render_markdown(inventory: list[dict[str, str]]) -> str:
    header = textwrap.dedent(
        """
        # Third-Party License Notices

        This project is distributed under the GNU General Public License version 3.0. In addition to the
        project's own license, the following third-party components are used at runtime. License
        information is collected from the Python Package Index (PyPI) and links point to the upstream
        license texts or project pages so downstream redistributors can comply with notice obligations.

        To refresh this inventory, run:

        ```bash
        python scripts/generate_third_party_licenses.py
        ```

        ## Inventory

        | Package | Version / Marker | License | License text or project page |
        | --- | --- | --- | --- |
        """
    ).strip()

    rows = [
        f"| `{item['name']}` | `{item['spec']}` | {item['license']} | [link]({item['license_url']}) |"
        for item in inventory
    ]

    footer = textwrap.dedent(
        """

        License information is sourced from upstream package metadata. If a license field is listed as
        "Unknown", consult the linked project page for definitive terms or update this inventory once
        the upstream project clarifies its license.
        """
    ).strip()

    return "\n".join([header, *rows, footer]) + "\n"


def main() -> int:
    inventory = build_inventory()
    DOC_PATH.write_text(render_markdown(inventory))
    print(f"Wrote {len(inventory)} entries to {DOC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
