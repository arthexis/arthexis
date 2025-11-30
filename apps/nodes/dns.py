from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, MutableMapping, TYPE_CHECKING

import requests
from django.utils import timezone
from dns import exception as dns_exception
from dns import resolver as dns_resolver
from requests import Response

if TYPE_CHECKING:  # pragma: no cover - imported for type checking only
    from .models import DNSRecord, NodeManager


@dataclass
class DeploymentResult:
    deployed: list["DNSRecord"]
    failures: MutableMapping["DNSRecord", str]
    skipped: MutableMapping["DNSRecord", str]


def _error_from_response(response: Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        for key in ("message", "detail", "error"):
            message = payload.get(key)
            if message:
                return str(message)
    elif isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, Mapping) and "message" in first:
            return str(first["message"])
        return str(first)

    reason = response.reason or ""
    return f"{response.status_code} {reason}".strip()


def deploy_records(manager: "NodeManager", records: Iterable["DNSRecord"]) -> DeploymentResult:
    filtered: list["DNSRecord"] = []
    skipped: MutableMapping["DNSRecord", str] = {}
    for record in records:
        if record.provider != record.Provider.GODADDY:
            skipped[record] = "Unsupported DNS provider"
            continue
        domain = record.get_domain(manager)
        if not domain:
            skipped[record] = "Domain is required for deployment"
            continue
        filtered.append(record)

    if not filtered:
        return DeploymentResult([], {}, skipped)

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": manager.get_auth_header(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    customer_id = manager.get_customer_id()
    if customer_id:
        session.headers["X-Shopper-Id"] = customer_id

    grouped: MutableMapping[tuple[str, str, str], list["DNSRecord"]] = defaultdict(list)
    for record in filtered:
        key = (
            record.get_domain(manager),
            record.record_type,
            record.get_name(),
        )
        grouped[key].append(record)

    deployed: list["DNSRecord"] = []
    failures: MutableMapping["DNSRecord", str] = {}
    now = timezone.now()

    base_url = manager.get_base_url()
    for (domain, record_type, name), grouped_records in grouped.items():
        payload = [record.to_godaddy_payload() for record in grouped_records]
        url = f"{base_url}/v1/domains/{domain}/records/{record_type}/{name or '@'}"
        try:
            response = session.put(url, json=payload, timeout=30)
        except requests.RequestException as exc:
            message = str(exc)
            for record in grouped_records:
                record.mark_error(message, manager=manager)
                failures[record] = message
            continue

        if response.status_code >= 400:
            message = _error_from_response(response)
            for record in grouped_records:
                record.mark_error(message, manager=manager)
                failures[record] = message
            continue

        for record in grouped_records:
            record.mark_deployed(manager=manager, timestamp=now)
            deployed.append(record)

    return DeploymentResult(deployed, failures, skipped)


def create_resolver() -> dns_resolver.Resolver:
    return dns_resolver.Resolver()


def _normalize_hostname(value: str) -> str:
    return value.rstrip(".").lower()


def _extract_txt(rdata) -> str:
    strings = getattr(rdata, "strings", None)
    if strings:
        parts = []
        for segment in strings:
            if isinstance(segment, bytes):
                parts.append(segment.decode("utf-8", "ignore"))
            else:
                parts.append(str(segment))
        return "".join(parts)
    return str(rdata).strip('"')


def _matches_record(record: "DNSRecord", rdata) -> bool:
    expected = (record.resolve_sigils("data") or "").strip()
    rtype = record.record_type

    if rtype == record.Type.A:
        return getattr(rdata, "address", str(rdata)) == expected
    if rtype == record.Type.AAAA:
        return getattr(rdata, "address", str(rdata)) == expected
    if rtype == record.Type.CNAME:
        actual = getattr(rdata, "target", None)
        if actual is None:
            return False
        return _normalize_hostname(actual.to_text()) == _normalize_hostname(expected)
    if rtype == record.Type.NS:
        actual = getattr(rdata, "target", None)
        if actual is None:
            return False
        return _normalize_hostname(actual.to_text()) == _normalize_hostname(expected)
    if rtype == record.Type.MX:
        host = getattr(rdata, "exchange", None)
        if host is None:
            return False
        host_match = _normalize_hostname(host.to_text()) == _normalize_hostname(expected)
        priority = getattr(rdata, "preference", None)
        priority_match = record.priority is None or priority == record.priority
        return host_match and priority_match
    if rtype == record.Type.SRV:
        target = getattr(rdata, "target", None)
        if target is None:
            return False
        target_match = _normalize_hostname(target.to_text()) == _normalize_hostname(expected)
        priority_match = record.priority is None or getattr(rdata, "priority", None) == record.priority
        weight_match = record.weight is None or getattr(rdata, "weight", None) == record.weight
        port_match = record.port is None or getattr(rdata, "port", None) == record.port
        return target_match and priority_match and weight_match and port_match
    if rtype == record.Type.TXT:
        actual = _extract_txt(rdata)
        return actual == expected

    return str(rdata).strip() == expected


def validate_record(
    record: "DNSRecord", resolver: dns_resolver.Resolver | None = None
) -> tuple[bool, str]:
    resolver = resolver or create_resolver()
    fqdn = record.fqdn()
    if not fqdn:
        message = "Domain is required for validation"
        record.mark_error(message)
        return False, message

    try:
        answers = resolver.resolve(fqdn, record.record_type)
    except (dns_exception.DNSException, OSError) as exc:
        message = str(exc)
        record.mark_error(message)
        return False, message

    for rdata in answers:
        if _matches_record(record, rdata):
            record.last_verified_at = timezone.now()
            record.last_error = ""
            record.save(update_fields=["last_verified_at", "last_error"])
            return True, ""

    message = "DNS record does not match expected value"
    record.mark_error(message)
    return False, message

