"""Service helpers for the public Evergo order tracking flow."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from django.core.files.uploadedfile import InMemoryUploadedFile

from apps.evergo.exceptions import EvergoAPIError

NA_IMAGE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5WQ6QAAAAASUVORK5CYII="
)


@dataclass(slots=True)
class TrackingSubmissionResult:
    """Normalized response details for each API phase submission."""

    order_payload: dict[str, Any]
    phase_1_status: int
    phase_1_payload: dict[str, Any]
    assign_status: int
    assign_payload: dict[str, Any]
    install_status: int
    install_payload: dict[str, Any]


def build_na_image(field_name: str) -> InMemoryUploadedFile:
    """Create a tiny PNG file used when the operator omits an image."""
    stream = BytesIO(NA_IMAGE_BYTES)
    return InMemoryUploadedFile(
        file=stream,
        field_name=field_name,
        name=f"{field_name}-na.png",
        content_type="image/png",
        size=len(NA_IMAGE_BYTES),
        charset=None,
    )


def ensure_image_payload(images: dict[str, InMemoryUploadedFile | None]) -> dict[str, InMemoryUploadedFile]:
    """Fill missing image entries with generated N/A images."""
    payload: dict[str, InMemoryUploadedFile] = {}
    for field_name, image in images.items():
        payload[field_name] = image if image is not None else build_na_image(field_name)
    return payload


def assert_success(status_code: int, phase_name: str) -> None:
    """Raise a specific exception when an Evergo phase call fails."""
    if status_code >= 400:
        raise EvergoAPIError(f"{phase_name} failed with status {status_code}.")
