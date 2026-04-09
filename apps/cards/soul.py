from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import mimetypes
import wave
import zipfile
from collections import Counter
from dataclasses import dataclass
from typing import BinaryIO
from zlib import compress

from django.core.files.uploadedfile import UploadedFile

try:
    from PIL import Image, UnidentifiedImageError
except ModuleNotFoundError:  # pragma: no cover - pillow is expected in runtime
    Image = None
    UnidentifiedImageError = Exception

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
RECOMMENDED_UPLOAD_BYTES = 10 * 1024 * 1024
PACKAGE_MAX_BYTES = 512 * 1024
TRAIT_SAMPLE_BYTES = 256 * 1024
EDGE_SIGNATURE_BYTES = 64
CHUNK_SIZE = 64 * 1024
SOUL_SCHEMA_VERSION = "1.0"


class SoulDerivationError(ValueError):
    """Raised when an offering cannot be deterministically derived."""


@dataclass(slots=True)
class OfferingMetadata:
    filename: str
    extension: str
    mime_type: str
    size_bytes: int


class RollingWindow:
    """Keep the most recent bytes without retaining full file contents."""

    def __init__(self, limit: int):
        self.limit = limit
        self.buffer = bytearray()

    def feed(self, payload: bytes) -> None:
        if not payload:
            return
        self.buffer.extend(payload)
        if len(self.buffer) > self.limit:
            del self.buffer[: len(self.buffer) - self.limit]

    def bytes(self) -> bytes:
        return bytes(self.buffer)


def _bucket_size(size_bytes: int) -> str:
    if size_bytes < 4 * 1024:
        return "tiny"
    if size_bytes < 64 * 1024:
        return "small"
    if size_bytes < 1024 * 1024:
        return "medium"
    if size_bytes < 10 * 1024 * 1024:
        return "large"
    return "huge"


def _shannon_entropy(histogram: list[int], total: int) -> float:
    if total <= 0:
        return 0.0
    entropy = 0.0
    for count in histogram:
        if count <= 0:
            continue
        p = count / total
        entropy -= p * math.log2(p)
    return round(entropy, 6)


def _histogram_digest(histogram: list[int], size_bytes: int) -> str:
    if size_bytes <= 0:
        return ""
    quantized = [min(255, int(round((count * 255) / size_bytes))) for count in histogram]
    return base64.urlsafe_b64encode(bytes(quantized)).decode("ascii").rstrip("=")


def _guess_mime(name: str, header: bytes) -> str:
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    header_signatures = (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"RIFF", "audio/wave"),
        (b"ID3", "audio/mpeg"),
        (b"OggS", "audio/ogg"),
        (b"fLaC", "audio/flac"),
        (b"PK\x03\x04", "application/zip"),
        (b"\x1f\x8b", "application/gzip"),
    )
    for prefix, detected in header_signatures:
        if header.startswith(prefix):
            return detected
    return "application/octet-stream"


def _hex_signature(payload: bytes) -> str:
    return binascii.hexlify(payload).decode("ascii")


def _text_profile(sample: bytes) -> dict[str, int | float]:
    if not sample:
        return {"line_count": 0, "max_line_bytes": 0, "avg_line_bytes": 0.0}
    lines = sample.splitlines() or [sample]
    lengths = [len(line) for line in lines]
    return {
        "line_count": len(lines),
        "max_line_bytes": max(lengths, default=0),
        "avg_line_bytes": round(sum(lengths) / max(len(lengths), 1), 3),
    }


def _detect_audio_profile(header: bytes, stream: BinaryIO) -> dict[str, object]:
    header_view = header[:16]
    if header_view.startswith(b"RIFF") and header_view[8:12] == b"WAVE":
        stream.seek(0)
        try:
            with wave.open(stream, "rb") as wav_file:
                frames = wav_file.getnframes()
                frame_rate = wav_file.getframerate()
                duration = round(frames / frame_rate, 3) if frame_rate else 0.0
                return {
                    "container": "wav",
                    "channels": wav_file.getnchannels(),
                    "sample_rate": frame_rate,
                    "duration_seconds": duration,
                }
        except (wave.Error, EOFError):
            return {"container": "wav", "malformed": True}
    if header_view.startswith(b"ID3"):
        return {"container": "mp3", "id3": True}
    if header_view.startswith(b"OggS"):
        return {"container": "ogg"}
    if header_view.startswith(b"fLaC"):
        return {"container": "flac"}
    return {}


def _detect_archive_traits(header: bytes, stream: BinaryIO) -> dict[str, object]:
    if header.startswith(b"PK\x03\x04"):
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream) as archive:
                compression_methods = Counter(info.compress_type for info in archive.infolist())
                methods = sorted(str(key) for key in compression_methods)
                return {
                    "container": "zip",
                    "entry_count": len(archive.infolist()),
                    "compression_methods": methods,
                }
        except zipfile.BadZipFile:
            return {"container": "zip", "malformed": True}
    if header.startswith(b"\x1f\x8b"):
        return {"container": "gzip"}
    return {}


def _detect_image_traits(stream: BinaryIO) -> dict[str, object]:
    if Image is None:
        return {}
    stream.seek(0)
    try:
        with Image.open(stream) as image:
            histogram = image.histogram()[:768]
            bins = [histogram[index : index + 32] for index in range(0, min(len(histogram), 768), 32)]
            digest_source = bytearray()
            for bucket in bins:
                digest_source.extend(int(min(255, sum(bucket) / max(len(bucket), 1))).to_bytes(1, "big"))
            return {
                "format": (image.format or "").lower(),
                "mode": image.mode,
                "width": image.width,
                "height": image.height,
                "exif_present": bool(getattr(image, "getexif", lambda: {})()),
                "color_digest": hashlib.sha256(bytes(digest_source)).hexdigest()[:16],
            }
    except (OSError, UnidentifiedImageError):
        return {}


def derive_soul_package(
    uploaded_file: UploadedFile,
    *,
    issuance_marker: str = "",
    schema_version: str = SOUL_SCHEMA_VERSION,
) -> dict[str, object]:
    """Derive a compact deterministic Soul package from an uploaded offering."""

    size_bytes = int(getattr(uploaded_file, "size", 0) or 0)
    if size_bytes <= 0:
        raise SoulDerivationError("Uploaded file is empty.")
    if size_bytes > MAX_UPLOAD_BYTES:
        raise SoulDerivationError(f"Uploaded file exceeds {MAX_UPLOAD_BYTES} bytes.")

    uploaded_file.seek(0)

    histogram = [0] * 256
    hasher = hashlib.sha256()
    printable_count = 0
    repeated_adjacent = 0
    total_bytes = 0
    run_length = 0
    max_run_length = 0
    prev_byte: int | None = None
    chunk_hashes: list[str] = []
    first_signature = bytearray()
    trailing_bytes = RollingWindow(EDGE_SIGNATURE_BYTES)
    sample = bytearray()
    header = b""

    for chunk in uploaded_file.chunks(CHUNK_SIZE):
        if not chunk:
            continue

        if not header:
            header = chunk[:512]

        hasher.update(chunk)
        chunk_hashes.append(hashlib.sha256(chunk).hexdigest()[:12])

        if len(first_signature) < EDGE_SIGNATURE_BYTES:
            remaining = EDGE_SIGNATURE_BYTES - len(first_signature)
            first_signature.extend(chunk[:remaining])

        trailing_bytes.feed(chunk)

        if len(sample) < TRAIT_SAMPLE_BYTES:
            remaining_sample = TRAIT_SAMPLE_BYTES - len(sample)
            sample.extend(chunk[:remaining_sample])

        for raw in chunk:
            histogram[raw] += 1
            total_bytes += 1
            if raw in (9, 10, 13) or 32 <= raw <= 126:
                printable_count += 1

            if prev_byte is None:
                run_length = 1
            elif raw == prev_byte:
                repeated_adjacent += 1
                run_length += 1
            else:
                max_run_length = max(max_run_length, run_length)
                run_length = 1
            prev_byte = raw

    max_run_length = max(max_run_length, run_length)

    filename = getattr(uploaded_file, "name", "") or ""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_type = _guess_mime(filename, header)

    metadata = OfferingMetadata(
        filename=filename,
        extension=extension,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )

    entropy = _shannon_entropy(histogram, total_bytes)
    printable_ratio = round(printable_count / total_bytes, 6)
    repeat_ratio = round(repeated_adjacent / max(total_bytes - 1, 1), 6)
    sample_bytes = bytes(sample)
    compressed = compress(sample_bytes, level=6)
    compressibility_ratio = round(len(compressed) / max(len(sample_bytes), 1), 6)

    structural_traits: dict[str, object] = {
        "size_bucket": _bucket_size(size_bytes),
        "entropy": entropy,
        "compressibility_ratio": compressibility_ratio,
        "histogram_digest": _histogram_digest(histogram, total_bytes),
        "first_bytes_hex": _hex_signature(bytes(first_signature)),
        "last_bytes_hex": _hex_signature(trailing_bytes.bytes()),
        "printable_ratio": printable_ratio,
        "repeat_ratio": repeat_ratio,
        "max_run_length": max_run_length,
        "chunk_profile": hashlib.sha256("|".join(chunk_hashes).encode("ascii")).hexdigest()[:24],
    }

    type_traits: dict[str, object] = {}

    if mime_type.startswith("image/"):
        type_traits["image"] = _detect_image_traits(uploaded_file)

    if printable_ratio >= 0.75:
        type_traits["text"] = _text_profile(sample_bytes)

    audio_traits = _detect_audio_profile(header, uploaded_file)
    if audio_traits:
        type_traits["audio"] = audio_traits

    archive_traits = _detect_archive_traits(header, uploaded_file)
    if archive_traits:
        type_traits["archive"] = archive_traits

    package = {
        "schema_version": schema_version,
        "hash_algorithm": "sha256",
        "core_hash": hasher.hexdigest(),
        "issuance_marker": issuance_marker,
        "metadata": {
            "filename": metadata.filename,
            "extension": metadata.extension,
            "mime_type": metadata.mime_type,
            "size_bytes": metadata.size_bytes,
            "recommended_size_exceeded": metadata.size_bytes > RECOMMENDED_UPLOAD_BYTES,
        },
        "traits": {
            "structural": structural_traits,
            "type_aware": type_traits,
        },
        "short_ids": {
            "core12": hasher.hexdigest()[:12],
            "nature8": hashlib.sha256(
                json.dumps(structural_traits, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:8],
        },
    }

    package_size = len(json.dumps(package, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if package_size > PACKAGE_MAX_BYTES:
        raise SoulDerivationError("Derived package exceeds 512 KB budget.")

    package["package_size_bytes"] = package_size
    return package
