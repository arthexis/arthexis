"""Durable SD-card burn queue helpers for the Raspberry Pi imager."""

from __future__ import annotations

import os
import posixpath
import shutil
import time
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.imager.models import RaspberryPiImageArtifact, RaspberryPiImageBurnJob
from apps.imager.services import (
    ImagerBuildError,
    _sha256_for_file,
    list_block_devices,
    write_image_to_device,
)
from apps.imager.services.models import BlockDeviceInfo, WriteResult
from apps.imager.usb_stability import quiet_usb_pollers
from apps.nodes.roles import node_is_control

IMAGER_BURNER_NODE_FEATURE_SLUG = "imager-burner"
IMAGER_BURNER_SERVICE_UNIT = "arthexis-imager-burner.service"
IMAGER_BURNER_LOG_LIMIT = 100_000
IMAGER_BURNER_RUNNING_STALE_SECONDS = 6 * 60 * 60
IMAGER_BURNER_PROGRESS_HEARTBEAT_SECONDS = 30


def has_imager_burner_tools() -> bool:
    """Return whether this host has the Linux tooling needed by the writer."""

    return os.name != "nt" and bool(shutil.which("lsblk"))


def imager_burner_available(*, node=None) -> bool:
    """Return whether the current node may own durable image burn jobs."""

    return bool(node is not None and node_is_control(node) and has_imager_burner_tools())


def device_identity(
    device: BlockDeviceInfo, *, requested_path: str
) -> dict[str, object]:
    """Return the safety-relevant target identity captured before queueing."""

    return {
        "path": device.path,
        "requested_path": requested_path,
        "identity_paths": list(device.identity_paths),
        "size_bytes": device.size_bytes,
        "transport": device.transport,
        "removable": device.removable,
        "protected": device.protected,
        "vendor": device.vendor,
        "model": device.model,
        "serial": device.serial,
        "partitions": list(device.partitions),
        "mountpoints": list(device.mountpoints),
        "write_blocked_reason": device.write_blocked_reason,
        "captured_at": timezone.now().isoformat(),
    }


def _canonical_device_identity_path(device_path: str) -> str:
    expanded = os.path.expanduser(device_path)
    normalized = posixpath.normpath(expanded)
    if expanded != normalized:
        return ""
    if not normalized.startswith("/dev/disk/by-id/"):
        return ""
    return normalized


def _stable_device_identity_path_for_request(
    *, device: BlockDeviceInfo, requested_path: str
) -> str:
    requested_identity_path = _canonical_device_identity_path(requested_path)
    if not requested_identity_path:
        raise ImagerBuildError(
            "Durable burn jobs require a stable /dev/disk/by-id/... target path."
        )

    discovered_identity_paths = {
        canonical_path
        for identity_path in device.identity_paths
        if (canonical_path := _canonical_device_identity_path(identity_path))
    }
    if requested_identity_path not in discovered_identity_paths:
        raise ImagerBuildError(
            "Durable burn jobs require a discovered /dev/disk/by-id/... target path."
        )
    return requested_identity_path


def _resolve_device_path(device_path: str) -> Path:
    return Path(device_path).expanduser().resolve(strict=False)


def _device_matches_requested_path(
    *, device: BlockDeviceInfo, requested_path: str
) -> bool:
    candidate_paths = [device.path, *device.identity_paths]
    if requested_path in candidate_paths:
        return True
    try:
        requested_resolved = _resolve_device_path(requested_path)
    except OSError:
        return False
    for candidate_path in candidate_paths:
        try:
            if _resolve_device_path(candidate_path) == requested_resolved:
                return True
        except OSError:
            continue
    return False


def _resolve_source(
    *,
    artifact_name: str = "",
    image_path: str = "",
) -> tuple[Path, RaspberryPiImageArtifact | None, str]:
    if bool(artifact_name) == bool(image_path):
        raise ImagerBuildError("Provide exactly one of --artifact or --image-path.")

    if artifact_name:
        artifact = RaspberryPiImageArtifact.objects.filter(name=artifact_name).first()
        if artifact is None:
            raise ImagerBuildError(f"Artifact '{artifact_name}' does not exist.")
        source_path = Path(artifact.output_path).expanduser().resolve()
        if not source_path.exists():
            raise ImagerBuildError(f"Artifact image file does not exist: {source_path}")
        return source_path, artifact, artifact.name

    source_path = Path(image_path).expanduser().resolve()
    if not source_path.exists():
        raise ImagerBuildError(f"Image file does not exist: {source_path}")
    return source_path, None, ""


def _find_device(device_path: str) -> BlockDeviceInfo:
    for device in list_block_devices():
        if _device_matches_requested_path(device=device, requested_path=device_path):
            return device
    raise ImagerBuildError(
        f"Target device '{device_path}' was not found in discovered block devices."
    )


def _preflight_device(
    device: BlockDeviceInfo, *, requested_path: str, source_size: int
) -> str:
    stable_identity_path = _stable_device_identity_path_for_request(
        device=device,
        requested_path=requested_path,
    )
    if not device.removable:
        raise ImagerBuildError(
            f"Refusing to queue non-removable media: {requested_path}"
        )
    if not device.serial:
        raise ImagerBuildError(
            f"Refusing to queue media without serial identity: {requested_path}"
        )
    if device.protected:
        raise ImagerBuildError(
            f"Refusing to queue protected system/root disk: {requested_path}"
        )
    if device.write_blocked_reason:
        raise ImagerBuildError(
            f"Refusing to queue blocked media '{requested_path}': "
            f"{device.write_blocked_reason}"
        )
    if device.mountpoints:
        mounts = ", ".join(device.mountpoints)
        raise ImagerBuildError(
            f"Refusing to queue mounted device '{requested_path}'. "
            f"Unmount all partitions first: {mounts}"
        )
    if device.size_bytes < source_size:
        raise ImagerBuildError(
            f"Target device '{requested_path}' is too small "
            f"({device.size_bytes} bytes) for image size {source_size} bytes."
        )
    return stable_identity_path


def _identity_mismatch(
    *,
    expected: dict[str, object],
    current: BlockDeviceInfo,
) -> str:
    comparisons = {
        "size_bytes": current.size_bytes,
        "vendor": current.vendor,
        "model": current.model,
        "serial": current.serial,
        "transport": current.transport,
        "removable": current.removable,
    }
    for key, current_value in comparisons.items():
        expected_value = expected.get(key)
        if expected_value in (None, ""):
            continue
        if str(expected_value) != str(current_value):
            return f"{key} changed from {expected_value!r} to {current_value!r}"
    return ""


def queue_burn_job(
    *,
    artifact_name: str = "",
    image_path: str = "",
    device_path: str,
    backup: bool = False,
    backup_dir: str = "",
) -> RaspberryPiImageBurnJob:
    """Create a durable burn job after source and device preflight checks."""

    source_path, artifact, normalized_artifact_name = _resolve_source(
        artifact_name=artifact_name,
        image_path=image_path,
    )
    source_size = source_path.stat().st_size
    source_sha256 = _sha256_for_file(source_path)
    device = _find_device(device_path)
    stable_device_path = _preflight_device(
        device,
        requested_path=device_path,
        source_size=source_size,
    )

    job = RaspberryPiImageBurnJob.objects.create(
        artifact=artifact,
        artifact_name=normalized_artifact_name,
        image_path="" if artifact is not None else str(source_path),
        image_sha256=source_sha256,
        image_size_bytes=source_size,
        progress_total_bytes=source_size,
        device_path=stable_device_path,
        device_identity=device_identity(device, requested_path=stable_device_path),
        backup=backup,
        backup_dir=backup_dir,
        log=(
            f"{timezone.now().isoformat()} queued "
            f"source={source_path} sha256={source_sha256} target={device.path}\n"
        ),
    )
    return job


def append_job_log(job: RaspberryPiImageBurnJob, message: str) -> None:
    """Append one timestamped log line to the persisted job log."""

    line = f"{timezone.now().isoformat()} {message.rstrip()}\n"
    job.log = f"{job.log}{line}"[-IMAGER_BURNER_LOG_LIMIT:]
    job.save(update_fields=["log", "updated_at"])


def _resolve_job_source(
    job: RaspberryPiImageBurnJob,
    *,
    progress_callback=None,
) -> tuple[Path, str, str]:
    source_path, _artifact, artifact_name = _resolve_source(
        artifact_name=job.artifact_name,
        image_path=job.image_path,
    )
    current_sha256 = _sha256_for_file(
        source_path,
        progress_callback=progress_callback,
    )
    if job.image_sha256 and current_sha256 != job.image_sha256:
        raise ImagerBuildError(
            "Queued image checksum changed before write: "
            f"expected {job.image_sha256}, got {current_sha256}."
        )
    return source_path, artifact_name, current_sha256


def claim_next_burn_job() -> RaspberryPiImageBurnJob | None:
    """Mark and return the oldest queued burn job for a worker process."""

    recover_stale_running_burn_jobs()

    while True:
        job = (
            RaspberryPiImageBurnJob.objects.filter(
                status=RaspberryPiImageBurnJob.Status.QUEUED
            )
            .order_by("created_at", "pk")
            .first()
        )
        if job is None:
            return None

        now = timezone.now()
        if not _claim_burn_job(job.pk, now=now):
            continue

        job.refresh_from_db()
        append_job_log(job, "worker claimed job")
        return job


def _claim_burn_job(job_id: int, *, now) -> bool:
    """Atomically transition a queued job to running for exactly one worker."""

    with transaction.atomic():
        claimed = RaspberryPiImageBurnJob.objects.filter(
            pk=job_id,
            status=RaspberryPiImageBurnJob.Status.QUEUED,
        ).update(
            status=RaspberryPiImageBurnJob.Status.RUNNING,
            attempts=F("attempts") + 1,
            started_at=now,
            finished_at=None,
            error="",
            progress_bytes=0,
            progress_total_bytes=F("image_size_bytes"),
            updated_at=now,
        )
    return bool(claimed)


def recover_stale_running_burn_jobs(
    *, stale_after_seconds: int = IMAGER_BURNER_RUNNING_STALE_SECONDS
) -> int:
    """Fail running jobs whose worker heartbeat has gone stale."""

    cutoff = timezone.now() - timedelta(seconds=stale_after_seconds)
    stale_jobs = list(
        RaspberryPiImageBurnJob.objects.filter(
            status=RaspberryPiImageBurnJob.Status.RUNNING,
            updated_at__lt=cutoff,
        ).order_by("updated_at", "pk")
    )
    for stale_job in stale_jobs:
        stale_job.status = RaspberryPiImageBurnJob.Status.FAILED
        stale_job.error = (
            "Burn worker heartbeat expired before a terminal result was recorded. "
            "Inspect the target media before queueing another write."
        )
        stale_job.finished_at = timezone.now()
        stale_job.save(
            update_fields=[
                "status",
                "finished_at",
                "error",
                "updated_at",
            ]
        )
        append_job_log(stale_job, "failed stale running job before claiming next job")
    return len(stale_jobs)


def _job_progress_heartbeat(job_id: int):
    last_heartbeat = 0.0

    def _heartbeat(written_bytes: int, source_size: int) -> None:
        nonlocal last_heartbeat
        now = time.monotonic()
        if (
            written_bytes < source_size
            and now - last_heartbeat < IMAGER_BURNER_PROGRESS_HEARTBEAT_SECONDS
        ):
            return
        last_heartbeat = now
        progress_bytes = max(0, int(written_bytes or 0))
        progress_total_bytes = max(0, int(source_size or 0))
        RaspberryPiImageBurnJob.objects.filter(
            pk=job_id,
            status=RaspberryPiImageBurnJob.Status.RUNNING,
        ).update(
            progress_bytes=progress_bytes,
            progress_total_bytes=progress_total_bytes,
            updated_at=timezone.now(),
        )

    return _heartbeat


def _job_liveness_heartbeat(job_id: int):
    last_heartbeat = 0.0

    def _heartbeat(_written_bytes: int, _source_size: int) -> None:
        nonlocal last_heartbeat
        now = time.monotonic()
        if now - last_heartbeat < IMAGER_BURNER_PROGRESS_HEARTBEAT_SECONDS:
            return
        last_heartbeat = now
        RaspberryPiImageBurnJob.objects.filter(
            pk=job_id,
            status=RaspberryPiImageBurnJob.Status.RUNNING,
        ).update(updated_at=timezone.now())

    return _heartbeat


def _write_result_payload(result: WriteResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "device_path": result.device_path,
        "image_path": str(result.image_path),
        "size_bytes": result.size_bytes,
        "source_sha256": result.source_sha256,
        "written_sha256": result.written_sha256,
        "verified": result.verified,
        "verified_at": timezone.now().isoformat(),
    }
    if result.backup is not None:
        payload["backup"] = {
            "path": str(result.backup.path),
            "size_bytes": result.backup.size_bytes,
            "sha256": result.backup.sha256,
            "verified": result.backup.verified,
        }
    return payload


def run_burn_job(job: RaspberryPiImageBurnJob) -> RaspberryPiImageBurnJob:
    """Execute one claimed burn job and persist the final result."""

    try:
        progress_callback = _job_progress_heartbeat(job.pk)
        with quiet_usb_pollers(log=lambda message: append_job_log(job, message)):
            source_path, artifact_name, _current_sha256 = _resolve_job_source(
                job,
                progress_callback=_job_liveness_heartbeat(job.pk),
            )
            current_device = _find_device(job.device_path)
            mismatch = _identity_mismatch(
                expected=job.device_identity,
                current=current_device,
            )
            if mismatch:
                raise ImagerBuildError(
                    f"Target device identity changed before write: {mismatch}."
                )
            _preflight_device(
                current_device,
                requested_path=job.device_path,
                source_size=job.image_size_bytes,
            )
            append_job_log(job, "source and target identity revalidated")
            result = write_image_to_device(
                device_path=job.device_path,
                artifact_name=artifact_name,
                image_path="" if artifact_name else str(source_path),
                confirmed=True,
                backup=job.backup,
                backup_dir=Path(job.backup_dir) if job.backup_dir else None,
                progress_callback=progress_callback,
            )
    except Exception as exc:
        job.status = RaspberryPiImageBurnJob.Status.FAILED
        job.error = str(exc)
        job.finished_at = timezone.now()
        job.save(
            update_fields=["status", "error", "finished_at", "updated_at"]
        )
        append_job_log(job, f"failed: {exc}")
        return job

    job.status = RaspberryPiImageBurnJob.Status.SUCCEEDED
    job.result = _write_result_payload(result)
    job.error = ""
    job.progress_total_bytes = result.size_bytes or job.progress_total_bytes or job.image_size_bytes
    job.progress_bytes = job.progress_total_bytes
    job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "result",
            "error",
            "progress_bytes",
            "progress_total_bytes",
            "finished_at",
            "updated_at",
        ]
    )
    append_job_log(job, "write and verification succeeded")
    return job


def work_once() -> RaspberryPiImageBurnJob | None:
    """Run one queued burn job when available."""

    job = claim_next_burn_job()
    if job is None:
        return None
    return run_burn_job(job)


def work_loop(*, interval: float) -> None:
    """Continuously run queued burn jobs for a systemd service worker."""

    while True:
        job = work_once()
        if job is None:
            time.sleep(interval)


def format_job_status(job: RaspberryPiImageBurnJob) -> str:
    """Return one operator-readable status line for a burn job."""

    source = job.artifact_name or job.image_path
    bits = [
        f"{job.uuid}",
        f"status={job.status}",
        f"source={source}",
        f"source_sha256={job.image_sha256 or '(unknown)'}",
        f"device={job.device_path}",
        f"attempts={job.attempts}",
    ]
    if job.progress_percent is not None:
        bits.append(f"progress={job.progress_percent}%")
    if job.error:
        bits.append(f"error={job.error}")
    if job.result:
        bits.append(f"verified={'yes' if job.result.get('verified') else 'no'}")
        bits.append(f"written_sha256={job.result.get('written_sha256') or '(unknown)'}")
    return " ".join(bits)


__all__ = [
    "IMAGER_BURNER_NODE_FEATURE_SLUG",
    "IMAGER_BURNER_SERVICE_UNIT",
    "append_job_log",
    "format_job_status",
    "has_imager_burner_tools",
    "imager_burner_available",
    "queue_burn_job",
    "recover_stale_running_burn_jobs",
    "run_burn_job",
    "work_loop",
    "work_once",
]
