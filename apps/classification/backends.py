"""Experimental training and inference backends for image classification."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Iterable

from django.conf import settings
from PIL import Image

from .models import ImageClassifierModel, TrainingSample


def resolve_media_file_path(media_file) -> Path:
    """Return the on-disk path for ``media_file``."""

    file_path = Path(media_file.file.path)
    if file_path.is_absolute():
        return file_path
    return Path(settings.MEDIA_ROOT) / file_path


@dataclass(frozen=True)
class TrainingArtifact:
    """Persisted output from a training backend."""

    backend: str
    storage_uri: str
    metrics: dict[str, object]


class ColorHistogramBackend:
    """Small histogram-based baseline for image-tag classification.

    This backend is intentionally simple. It is not a production-quality model,
    but it gives the suite a real vertical slice for: training from examples,
    persisting an artifact, loading it later, and scoring new camera frames.
    """

    slug = "color_histogram"
    bins = (8, 8, 8)

    def _artifact_dir(self, classifier: ImageClassifierModel) -> Path:
        base_dir = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media"))
        artifact_dir = base_dir / "classifier-artifacts" / classifier.slug
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir

    def _artifact_path(self, classifier: ImageClassifierModel) -> Path:
        return self._artifact_dir(classifier) / f"{classifier.version}.json"

    def _compute_histogram(self, image_path: Path) -> list[float]:
        try:
            image = Image.open(image_path).convert("RGB")
        except Exception as exc:
            raise ValueError(f"Unable to read image at {image_path}") from exc

        red_bins, green_bins, blue_bins = self.bins
        histogram = [0.0] * (red_bins * green_bins * blue_bins)
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError(f"Image at {image_path} did not contain readable pixels.")

        pixels = image.load()
        for x in range(width):
            for y in range(height):
                red, green, blue = pixels[x, y]
                red_index = min(red_bins - 1, red * red_bins // 256)
                green_index = min(green_bins - 1, green * green_bins // 256)
                blue_index = min(blue_bins - 1, blue * blue_bins // 256)
                flat_index = (red_index * green_bins + green_index) * blue_bins + blue_index
                histogram[flat_index] += 1.0

        total = float(width * height)
        return [value / total for value in histogram]

    def _average_vectors(self, vectors: list[list[float]]) -> list[float]:
        if not vectors:
            return []
        width = len(vectors[0])
        totals = [0.0] * width
        for vector in vectors:
            for index, value in enumerate(vector):
                totals[index] += value
        return [value / len(vectors) for value in totals]

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        dot_product = sum(left_value * right_value for left_value, right_value in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return dot_product / (left_norm * right_norm)

    def train(
        self,
        *,
        classifier: ImageClassifierModel,
        samples: Iterable[TrainingSample],
    ) -> TrainingArtifact:
        """Train a tag centroid from verified image examples."""

        vectors_by_tag: dict[str, list[list[float]]] = {}
        for sample in samples:
            vector = self._compute_histogram(resolve_media_file_path(sample.media_file))
            vectors_by_tag.setdefault(sample.tag.slug, []).append(vector)

        if not vectors_by_tag:
            raise ValueError("No readable training samples were available.")

        payload = {
            "backend": self.slug,
            "bins": list(self.bins),
            "tags": {},
        }
        for tag_slug, vectors in vectors_by_tag.items():
            centroid = self._average_vectors(vectors)
            payload["tags"][tag_slug] = {
                "sample_count": len(vectors),
                "vector": centroid,
            }

        artifact_path = self._artifact_path(classifier)
        artifact_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        metrics = {
            "backend": self.slug,
            "sample_count": int(sum(len(vectors) for vectors in vectors_by_tag.values())),
            "tag_count": len(vectors_by_tag),
            "samples_by_tag": {
                tag_slug: len(vectors) for tag_slug, vectors in vectors_by_tag.items()
            },
        }
        return TrainingArtifact(
            backend=self.slug,
            storage_uri=artifact_path.as_posix(),
            metrics=metrics,
        )

    def _load_payload(self, classifier: ImageClassifierModel) -> dict[str, object]:
        if not classifier.storage_uri:
            raise ValueError("Classifier does not have a stored artifact yet.")
        payload = json.loads(Path(classifier.storage_uri).read_text(encoding="utf-8"))
        if payload.get("backend") != self.slug:
            raise ValueError(
                f"Classifier artifact backend mismatch: expected {self.slug}, got {payload.get('backend')}"
            )
        return payload

    def predict(
        self,
        *,
        classifier: ImageClassifierModel,
        media_file,
        top_k: int = 3,
    ) -> list[dict[str, object]]:
        """Score ``media_file`` against the persisted tag centroids."""

        payload = self._load_payload(classifier)
        vector = self._compute_histogram(resolve_media_file_path(media_file))
        predictions: list[dict[str, object]] = []
        for tag_slug, tag_payload in dict(payload.get("tags") or {}).items():
            centroid = list(tag_payload.get("vector") or [])
            if not centroid:
                continue
            score = self._cosine_similarity(vector, centroid)
            confidence = max(0.0, min(1.0, score))
            predictions.append(
                {
                    "tag": tag_slug,
                    "confidence": round(confidence, 4),
                    "metadata": {
                        "backend": self.slug,
                        "score": round(score, 6),
                        "sample_count": int(tag_payload.get("sample_count") or 0),
                    },
                }
            )
        predictions.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
        return predictions[: max(int(top_k), 1)]


BACKENDS = {
    ColorHistogramBackend.slug: ColorHistogramBackend(),
}


def resolve_backend(classifier: ImageClassifierModel):
    """Return the backend configured for ``classifier``."""

    backend_slug = str((classifier.training_parameters or {}).get("backend") or ColorHistogramBackend.slug)
    backend = BACKENDS.get(backend_slug)
    if backend is None:
        raise ValueError(f"Unsupported classifier backend '{backend_slug}'")
    return backend
