from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.media.models import MediaFile
from apps.media.utils import create_media_file


class MediaUploadAdminFormMixin:
    """Reusable helpers for admin forms that support existing media or uploads."""

    media_upload_bindings: dict[str, dict[str, Any]] = {}

    def setup_bucket_aware_querysets(self) -> None:
        for binding in self.media_upload_bindings.values():
            media_field_name = binding["media_field"]
            bucket = self.get_media_bucket(binding["bucket_provider"])
            self.fields[media_field_name].queryset = MediaFile.objects.filter(bucket=bucket)

    def get_media_bucket(self, bucket_provider: Callable[[], Any]) -> Any:
        cache = getattr(self, "_media_bucket_cache", None)
        if cache is None:
            cache = {}
            self._media_bucket_cache = cache
        if bucket_provider not in cache:
            cache[bucket_provider] = bucket_provider()
        return cache[bucket_provider]

    def validate_bucket_upload(self, upload: Any, *, bucket_provider: Callable[[], Any]) -> Any:
        if upload:
            bucket = self.get_media_bucket(bucket_provider)
            if not bucket.allows_filename(upload.name):
                raise forms.ValidationError(_("File type is not allowed."))
            if not bucket.allows_size(upload.size):
                raise forms.ValidationError(_("File exceeds the allowed size."))
        return upload

    def clean_upload_field(self, upload_field_name: str) -> Any:
        upload = self.cleaned_data.get(upload_field_name)
        binding = self.media_upload_bindings[upload_field_name]
        upload = self.validate_bucket_upload(upload, bucket_provider=binding["bucket_provider"])
        validator = binding.get("extra_validator")
        if upload and validator:
            validator(upload)
        return upload

    def store_uploads_on_instance(self, instance: Any) -> Any:
        for upload_field_name, binding in self.media_upload_bindings.items():
            upload = self.cleaned_data.get(upload_field_name)
            if not upload:
                continue
            bucket = self.get_media_bucket(binding["bucket_provider"])
            media_file = create_media_file(bucket=bucket, uploaded_file=upload)
            setattr(instance, binding["media_field"], media_file)
        return instance
