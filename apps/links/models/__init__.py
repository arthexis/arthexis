"""Public model exports for the links app."""

from .redirects import QRRedirect, QRRedirectLead
from .reference import (
    ExperienceReference,
    REFERENCE_FILE_ALLOWED_PATTERNS,
    REFERENCE_FILE_BUCKET_SLUG,
    REFERENCE_QR_ALLOWED_PATTERNS,
    REFERENCE_QR_BUCKET_SLUG,
    Reference,
    ReferenceManager,
    get_reference_file_bucket,
    get_reference_qr_bucket,
)
from .reference_attachment import ReferenceAttachment
from .short_url import ShortURL, get_or_create_short_url
from .validators import (
    _is_valid_redirect_target,
    generate_qr_slug,
    generate_short_slug,
)

_generate_qr_slug = generate_qr_slug
_generate_short_slug = generate_short_slug

__all__ = [
    "_generate_qr_slug",
    "_generate_short_slug",
    "_is_valid_redirect_target",
    "ExperienceReference",
    "QRRedirect",
    "QRRedirectLead",
    "ReferenceAttachment",
    "REFERENCE_FILE_ALLOWED_PATTERNS",
    "REFERENCE_FILE_BUCKET_SLUG",
    "REFERENCE_QR_ALLOWED_PATTERNS",
    "REFERENCE_QR_BUCKET_SLUG",
    "Reference",
    "ReferenceManager",
    "ShortURL",
    "get_or_create_short_url",
    "get_reference_file_bucket",
    "get_reference_qr_bucket",
]
