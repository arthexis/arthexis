from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.utils.translation import gettext_lazy as _


class MaxContentDropUploadSizeHandler(FileUploadHandler):
    """Abort admin drag-and-drop uploads once they exceed a configured limit.

    Parameters:
        request: Django request receiving the upload stream.
        max_size: Maximum allowed upload size in bytes.

    Raises:
        StopUpload: When the upload grows past ``max_size``.
    """

    def __init__(self, request, *, max_size: int):
        """Initialize the handler with the active request and size cap.

        Parameters:
            request: Django request receiving the upload stream.
            max_size: Maximum allowed upload size in bytes.
        """

        super().__init__(request=request)
        self.max_size = max_size

    def receive_data_chunk(self, raw_data: bytes, start: int) -> bytes:
        """Inspect an incoming chunk and stop parsing when the limit is crossed.

        Parameters:
            raw_data: Raw upload bytes for the current chunk.
            start: Zero-based byte offset for the start of ``raw_data``.

        Returns:
            The original ``raw_data`` so later handlers can persist bounded data.

        Raises:
            StopUpload: If the current chunk would push the upload beyond
                ``self.max_size``.
        """

        if start + len(raw_data) > self.max_size:
            self.request.content_drop_upload_error = ValidationError(
                _("File exceeds the allowed size.")
            )
            raise StopUpload(connection_reset=False)
        return raw_data

    def file_complete(self, file_size: int):
        """Finish the upload without creating a file object in this handler.

        Parameters:
            file_size: Total size accumulated for the file.

        Returns:
            ``None`` so Django continues using subsequent upload handlers.
        """

        return None
