"""Recovery access-point addressing for imaged Arthexis nodes."""

from __future__ import annotations


# The second octet identifies the hostname/customer namespace. Gway keeps the
# historical 42 allocation; customer-chosen namespaces can be assigned fixed
# values from 43 onward without changing the addressing algorithm.
AP_NAMESPACE_OCTETS: dict[str, int] = {
    "gway": 42,
}


def recovery_ap_address(hostname_prefix: str, number: int) -> str:
    """Return ``10.<namespace>.<device>.1/24`` for a reserved node identity."""

    prefix = (hostname_prefix or "").strip().lower()
    try:
        namespace_octet = AP_NAMESPACE_OCTETS[prefix]
    except KeyError as exc:
        raise ValueError(
            f"No recovery AP namespace is allocated for hostname prefix {prefix!r}. "
            "Add it to AP_NAMESPACE_OCTETS before imaging that customer namespace."
        ) from exc

    if not 1 <= namespace_octet <= 254:
        raise ValueError(
            f"Recovery AP namespace octet for {prefix!r} must be between 1 and 254."
        )
    if not 1 <= number <= 254:
        raise ValueError("Recovery AP device number must be between 1 and 254.")

    return f"10.{namespace_octet}.{number}.1/24"
