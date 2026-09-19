"""OCPP 2.0.1 certificate-management request validation."""

from apps.ocpp.protocol.v201.outbound.common import require

VALIDATORS = {
    "CertificateSigned": lambda payload: require(payload, "certificateChain"),
    "DeleteCertificate": lambda payload: require(payload, "certificateHashData"),
    "GetInstalledCertificateIds": lambda payload: require(payload, "certificateType"),
    "InstallCertificate": lambda payload: require(
        payload, "certificate", "certificateType"
    ),
}
