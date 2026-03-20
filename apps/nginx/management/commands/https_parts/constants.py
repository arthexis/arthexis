"""Shared constants for the HTTPS management command."""

FORCE_RENEWAL_EXPIRATION_UNAVAILABLE_WARNING = (
    "--force-renewal completed but certificate expiration could not be determined for {domain}. "
    "CertbotCertificate.request may have continued after services.get_certificate_expiration failed. "
    "Inspect certbot logs and DNS challenge status, then verify the certificate manually."
)
FORCE_RENEWAL_STILL_EXPIRED_ERROR = (
    "--force-renewal completed but the certificate is still expired. "
    "Inspect certbot logs and DNS challenge status, then retry."
)
CERTBOT_HTTP01_BOOTSTRAP_MESSAGE = (
    "The HTTP-01 challenge requires an active nginx site entry for this domain. "
    "Arthexis attempted to stage and apply an HTTP site configuration automatically before requesting the certificate."
)
NGINX_CONFIGURE_REMEDIATION_TEMPLATE = (
    "Run '{command} nginx --configure' to bootstrap nginx management on this node, "
    "then rerun the HTTPS command."
)
