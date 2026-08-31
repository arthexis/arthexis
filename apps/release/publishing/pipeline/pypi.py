"""PyPI release availability and Trusted Publisher gate helpers."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import requests
import yaml
from django.utils import timezone
from packaging.version import InvalidVersion, Version

EXPECTED_PUBLISH_WORKFLOW_FILE = "publish.yml"
EXPECTED_PUBLISH_REF_PATTERN = "refs/tags/v*"
EXPECTED_PUBLISH_ENVIRONMENT = "pypi"

AppendLog = Callable[[Path, str], None]
FailReleaseGate = Callable[[dict, Path, str], NoReturn]
NetworkAvailable = Callable[[], bool]
RequestsGet = Callable[..., requests.Response]


def check_release_version_not_on_pypi(
    release,
    log_path: Path,
    *,
    append_log: AppendLog,
    network_available: NetworkAvailable,
    request_timeout: float,
    requests_get: RequestsGet = requests.get,
) -> None:
    append_log(log_path, f"Checking if version {release.version} exists on PyPI")
    if not network_available():
        append_log(log_path, "Network unavailable, skipping PyPI check")
        return

    resp = None
    try:
        resp = requests_get(
            f"https://pypi.org/pypi/{release.package.name}/json",
            timeout=request_timeout,
        )
        if not resp.ok:
            return

        data = resp.json()
        releases = data.get("releases", {})
        try:
            target_version = Version(release.version)
        except InvalidVersion:
            target_version = None

        for candidate, files in releases.items():
            if not versions_match(
                candidate=candidate,
                release_version=release.version,
                target_version=target_version,
            ):
                continue

            if has_non_yanked_files(files):
                raise RuntimeError(f"Version {release.version} already on PyPI")
    except RuntimeError:
        raise
    except (requests.exceptions.RequestException, ValueError) as exc:
        append_log(log_path, f"PyPI check failed: {exc}")
        return
    else:
        append_log(log_path, f"Version {release.version} not published on PyPI")
    finally:
        if resp is not None:
            resp.close()


def versions_match(
    *,
    candidate: str,
    release_version: str,
    target_version: Version | None,
) -> bool:
    if candidate == release_version:
        return True
    if target_version is None:
        return False

    try:
        return Version(candidate) == target_version
    except InvalidVersion:
        return False


def has_non_yanked_files(files: object) -> bool:
    return any(
        isinstance(file_data, dict) and not file_data.get("yanked", False)
        for file_data in files or []
    )


def confirm_pypi_trusted_publisher_settings(
    release,
    ctx: dict,
    log_path: Path,
    *,
    append_log: AppendLog,
    fail_release_gate: FailReleaseGate,
    now: Callable[[], object] = timezone.now,
) -> None:
    _ = release
    append_log(log_path, "Confirm PyPI Trusted Publisher settings")
    workflow_path = Path(".github/workflows") / EXPECTED_PUBLISH_WORKFLOW_FILE
    if not workflow_path.exists():
        fail_release_gate(
            ctx,
            log_path,
            f"Trusted Publisher gate failed: {workflow_path} is missing. "
            "Add the publish workflow before publishing.",
        )

    workflow_data: dict = {}
    yaml_error = False
    try:
        loaded_workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        if isinstance(loaded_workflow, dict):
            workflow_data = loaded_workflow
    except yaml.YAMLError:
        yaml_error = True

    mismatches: list[str] = []
    if yaml_error:
        mismatches.append(
            f"workflow YAML in {workflow_path} must be valid and parseable"
        )

    on_section = workflow_data.get("on", workflow_data.get(True, {}))
    push_section = on_section.get("push", {}) if isinstance(on_section, dict) else {}
    raw_tags = push_section.get("tags") if isinstance(push_section, dict) else None
    tags: list[str] = []
    if isinstance(raw_tags, str):
        tags = [raw_tags]
    elif isinstance(raw_tags, list):
        tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
    elif raw_tags is not None:
        tags = [str(raw_tags).strip()] if str(raw_tags).strip() else []
    if raw_tags is None or not tags:
        mismatches.append(
            f"{workflow_path} must define non-empty on.push.tags"
            " (missing key: on.push.tags)"
        )
    expected_tag = EXPECTED_PUBLISH_REF_PATTERN.removeprefix("refs/tags/")
    if tags and set(tags) != {expected_tag}:
        mismatches.append(
            f"workflow tag pattern must be {EXPECTED_PUBLISH_REF_PATTERN} "
            f"(check key: on.push.tags in {workflow_path})"
        )

    jobs = workflow_data.get("jobs", {})
    publish_job = jobs.get("publish-to-pypi", {}) if isinstance(jobs, dict) else {}
    if not isinstance(publish_job, dict) or not publish_job:
        mismatches.append(
            f"{workflow_path} must define jobs.publish-to-pypi"
            " (missing key: jobs.publish-to-pypi)"
        )

    environment = (
        publish_job.get("environment", "") if isinstance(publish_job, dict) else ""
    )
    observed_environment_name = ""
    if isinstance(environment, str):
        observed_environment_name = environment.strip()
    elif isinstance(environment, dict):
        observed_environment_name = str(environment.get("name", "")).strip()
    if not observed_environment_name:
        mismatches.append(
            f"{workflow_path} must define non-empty publish job environment.name"
            " (missing key: jobs.publish-to-pypi.environment.name)"
        )
    elif observed_environment_name != EXPECTED_PUBLISH_ENVIRONMENT:
        mismatches.append(
            f"workflow environment must be {EXPECTED_PUBLISH_ENVIRONMENT} "
            f"(check key: jobs.publish-to-pypi.environment.name in {workflow_path})"
        )

    job_permissions = (
        publish_job.get("permissions") if isinstance(publish_job, dict) else None
    )
    permissions = (
        job_permissions
        if job_permissions is not None
        else workflow_data.get("permissions", {})
    )
    id_token_permission = ""
    if isinstance(permissions, dict):
        id_token_permission = str(permissions.get("id-token", "")).strip()
    elif isinstance(permissions, str) and permissions == "write-all":
        id_token_permission = "write"
    if id_token_permission != "write":
        mismatches.append(
            f"{workflow_path} must set jobs.publish-to-pypi.permissions.id-token to"
            " 'write' (missing/invalid key: jobs.publish-to-pypi.permissions.id-token)"
        )

    steps = publish_job.get("steps", []) if isinstance(publish_job, dict) else []
    uses_entries = []
    if isinstance(steps, list):
        uses_entries = [
            str(step.get("uses", "")).strip()
            for step in steps
            if isinstance(step, dict) and str(step.get("uses", "")).strip()
        ]
    has_publish_action = any(
        action.startswith("pypa/gh-action-pypi-publish@")
        or action == "pypa/gh-action-pypi-publish"
        for action in uses_entries
    )
    if not has_publish_action:
        mismatches.append(
            f"{workflow_path} must include pypa/gh-action-pypi-publish in"
            " jobs.publish-to-pypi.steps[*].uses"
            " (missing key family: jobs.publish-to-pypi.steps[*].uses)"
        )

    static_token_keys = (
        "password",
        "token",
        "api_token",
        "repository_password",
        "user",
        "username",
    )
    has_static_token_field = False
    has_non_oidc_publish_path = False
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses", "")).strip()
        if uses:
            normalized_uses = uses.lower()
            if (
                "pypa/gh-action-pypi-publish" not in normalized_uses
                and "pypi-publish" in normalized_uses
            ):
                has_non_oidc_publish_path = True
                break
        run_command = str(step.get("run", "")).strip().lower()
        if "twine upload" in run_command:
            has_non_oidc_publish_path = True
            break
        if not (
            uses.startswith("pypa/gh-action-pypi-publish@")
            or uses == "pypa/gh-action-pypi-publish"
        ):
            continue
        step_with = step.get("with", {})
        if not isinstance(step_with, dict):
            continue
        if any(str(step_with.get(key, "")).strip() for key in static_token_keys):
            has_static_token_field = True
            break
    if has_static_token_field:
        mismatches.append(
            f"{workflow_path} must not set static token credentials in"
            " jobs.publish-to-pypi.steps[*].with when Trusted Publisher OIDC is expected"
            " (remove keys like password/token/api_token)"
        )
    if has_non_oidc_publish_path:
        mismatches.append(
            f"{workflow_path} jobs.publish-to-pypi.steps must use only"
            " pypa/gh-action-pypi-publish for package upload"
            " (remove twine upload and other publish actions)"
        )

    if mismatches:
        fail_release_gate(
            ctx,
            log_path,
            "Trusted Publisher gate failed: " + "; ".join(mismatches) + ".",
        )

    ctx["trusted_publisher_verified_at"] = now().isoformat()
    ctx["trusted_publisher_workflow_file"] = EXPECTED_PUBLISH_WORKFLOW_FILE
    ctx["trusted_publisher_ref"] = EXPECTED_PUBLISH_REF_PATTERN
    ctx["trusted_publisher_environment"] = EXPECTED_PUBLISH_ENVIRONMENT
    append_log(
        log_path,
        "Trusted Publisher gate passed "
        f"(workflow={EXPECTED_PUBLISH_WORKFLOW_FILE}, "
        f"ref={EXPECTED_PUBLISH_REF_PATTERN}, environment={EXPECTED_PUBLISH_ENVIRONMENT})",
    )


def pypi_release_available(
    release,
    *,
    network_available: NetworkAvailable,
    request_timeout: float,
    requests_get: RequestsGet = requests.get,
) -> bool:
    if not network_available():
        return False
    resp = None
    try:
        resp = requests_get(
            f"https://pypi.org/pypi/{release.package.name}/json",
            timeout=request_timeout,
        )
        if not resp.ok:
            return False
        data = resp.json()
        releases = data.get("releases", {})
        try:
            target_version = Version(release.version)
        except InvalidVersion:
            target_version = None
        for candidate, files in releases.items():
            if not versions_match(
                candidate=candidate,
                release_version=release.version,
                target_version=target_version,
            ):
                continue
            if has_non_yanked_files(files):
                return True
        return False
    except (requests.exceptions.RequestException, ValueError):
        return False
    finally:
        if resp is not None:
            with contextlib.suppress(Exception):
                resp.close()
