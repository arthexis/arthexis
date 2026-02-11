"""Certificate lifecycle call result handlers."""

from __future__ import annotations

from . import legacy
from .common import HandlerContext, legacy_adapter


async def install_certificate(ctx: HandlerContext) -> bool:
    """Handle InstallCertificate responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: updates ``CertificateOperation`` and ``InstalledCertificate`` state.
    """

    return await legacy.handle_install_certificate_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def delete_certificate(ctx: HandlerContext) -> bool:
    """Handle DeleteCertificate responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: updates ``CertificateOperation`` and marks installed cert as deleted/rejected/error.
    """

    return await legacy.handle_delete_certificate_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def certificate_signed(ctx: HandlerContext) -> bool:
    """Handle CertificateSigned responses.

    Expected payload keys: ``status`` and optional ``statusInfo``.
    Persistence updates: updates ``CertificateOperation`` status and payload.
    """

    return await legacy.handle_certificate_signed_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


async def get_installed_certificate_ids(ctx: HandlerContext) -> bool:
    """Handle GetInstalledCertificateIds responses.

    Expected payload keys: ``status`` and optional ``certificateHashData`` entries.
    Persistence updates: updates ``CertificateOperation`` and upserts ``InstalledCertificate`` rows.
    """

    return await legacy.handle_get_installed_certificate_ids_result(ctx.consumer, ctx.message_id, ctx.metadata, ctx.payload, ctx.log_key)


handle_install_certificate_result = legacy_adapter(install_certificate)
handle_delete_certificate_result = legacy_adapter(delete_certificate)
handle_certificate_signed_result = legacy_adapter(certificate_signed)
handle_get_installed_certificate_ids_result = legacy_adapter(get_installed_certificate_ids)
