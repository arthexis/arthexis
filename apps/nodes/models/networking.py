from __future__ import annotations

from collections.abc import Iterable
import ipaddress
import socket
from urllib.parse import urlparse, urlunsplit


class NodeNetworkingMixin:
    """Networking helpers for :class:`Node`."""

    def prefers_https(self, *, require_https: bool | None = None) -> bool:
        if require_https is not None:
            return require_https
        site = getattr(self, "base_site", None)
        if site and bool(getattr(site, "require_https", False)):
            return True
        if getattr(self, "port", None) == 443:
            return True
        return False

    def iter_preferred_schemes(
        self,
        *,
        default: str = "http",
        require_https: bool | None = None,
    ) -> tuple[str, ...]:
        if self.prefers_https(require_https=require_https):
            return ("https",)
        if default == "https":
            return ("https", "http")
        return ("http", "https")

    def get_preferred_scheme(
        self,
        *,
        default: str = "http",
        require_https: bool | None = None,
    ) -> str:
        return "https" if self.prefers_https(require_https=require_https) else default

    @staticmethod
    def _ip_preference(ip_value: str) -> tuple[int, str]:
        """Return a sort key favouring globally routable addresses."""

        try:
            parsed = ipaddress.ip_address(ip_value)
        except ValueError:
            return (3, ip_value)

        if parsed.is_global:
            return (0, ip_value)

        if parsed.is_loopback or parsed.is_link_local:
            return (2, ip_value)

        if parsed.is_private:
            return (2, ip_value)

        return (1, ip_value)

    @classmethod
    def _select_preferred_ip(cls, addresses: Iterable[str]) -> str | None:
        """Return the preferred IP from ``addresses`` when available."""

        best: tuple[int, str] | None = None
        for candidate in addresses:
            candidate = (candidate or "").strip()
            if not candidate:
                continue
            score = cls._ip_preference(candidate)
            if best is None or score < best:
                best = score
        return best[1] if best else None

    @classmethod
    def _iter_ipv4_inputs(cls, values) -> Iterable[str]:
        if values is None:
            return []
        if isinstance(values, str):
            tokens = values.replace(";", ",").split(",")
            return [token.strip() for token in tokens if token.strip()]
        if isinstance(values, Iterable):
            flattened: list[str] = []
            for item in values:
                flattened.extend(cls._iter_ipv4_inputs(item))
            return flattened
        return [str(values).strip()]

    @classmethod
    def sanitize_ipv4_addresses(cls, values) -> list[str]:
        """Return a normalized list of IPv4 addresses without local entries."""

        cleaned: list[str] = []
        for token in cls._iter_ipv4_inputs(values):
            if not token:
                continue
            try:
                parsed = ipaddress.ip_address(token)
            except ValueError:
                continue
            if parsed.version != 4:
                continue
            if parsed.is_loopback or parsed.is_unspecified:
                continue
            normalized = str(parsed)
            if normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned

    @classmethod
    def order_ipv4_addresses(cls, addresses: Iterable[str]) -> list[str]:
        ordered: list[tuple[int, str]] = []
        for index, value in enumerate(addresses):
            score = cls._ip_preference(value)[0]
            ordered.append((score, index, value))
        ordered.sort()
        return [value for _, _, value in ordered]

    @classmethod
    def serialize_ipv4_addresses(cls, values) -> str | None:
        cleaned = cls.sanitize_ipv4_addresses(values)
        if not cleaned:
            return None
        ordered = cls.order_ipv4_addresses(cleaned)
        return ",".join(ordered)

    def get_ipv4_addresses(self) -> list[str]:
        stored = self.ipv4_address or ""
        cleaned = self.sanitize_ipv4_addresses(stored)
        return self.order_ipv4_addresses(cleaned)

    @classmethod
    def _resolve_ip_addresses(
        cls, *hosts: str, include_ipv4: bool = True, include_ipv6: bool = True
    ) -> tuple[list[str], list[str]]:
        """Resolve ``hosts`` into IPv4 and IPv6 address lists."""

        ipv4: list[str] = []
        ipv6: list[str] = []

        for host in hosts:
            host = (host or "").strip()
            if not host:
                continue
            try:
                info = socket.getaddrinfo(
                    host,
                    None,
                    socket.AF_UNSPEC,
                    socket.SOCK_STREAM,
                )
            except OSError:
                continue
            for family, _, _, _, sockaddr in info:
                if family == socket.AF_INET and include_ipv4:
                    value = sockaddr[0]
                    if value not in ipv4:
                        ipv4.append(value)
                elif family == socket.AF_INET6 and include_ipv6:
                    value = sockaddr[0]
                    if value not in ipv6:
                        ipv6.append(value)

        return ipv4, ipv6

    def get_remote_host_candidates(self, *, resolve_dns: bool = True) -> list[str]:
        """Return host strings that may reach this node.

        ``resolve_dns`` controls whether hostnames are expanded to IP addresses.
        DNS lookups can be slow in contexts like admin changelists, so callers
        can disable resolution when they only need stored values.
        """

        values: list[str] = []
        base_domain = self.get_base_domain()
        if base_domain:
            values.append(base_domain)
        for attr in (
            "network_hostname",
            "hostname",
            "ipv6_address",
            "ipv4_address",
            "address",
        ):
            if attr == "ipv4_address":
                for candidate in self.get_ipv4_addresses():
                    if candidate not in values:
                        values.append(candidate)
                continue
            value = getattr(self, attr, "") or ""
            value = value.strip()
            if value and value not in values:
                values.append(value)

        if resolve_dns:
            resolved_ipv6: list[str] = []
            resolved_ipv4: list[str] = []
            for host in list(values):
                if host.startswith("http://") or host.startswith("https://"):
                    continue
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    ipv4, ipv6 = self._resolve_ip_addresses(host)
                    for candidate in ipv6:
                        if candidate not in values and candidate not in resolved_ipv6:
                            resolved_ipv6.append(candidate)
                    for candidate in ipv4:
                        if candidate not in values and candidate not in resolved_ipv4:
                            resolved_ipv4.append(candidate)
            values.extend(resolved_ipv6)
            values.extend(resolved_ipv4)
        return values

    def get_primary_contact(self) -> str:
        """Return the first reachable host for this node."""

        for host in self.get_remote_host_candidates():
            if host:
                return host
        return ""

    def get_best_ip(self) -> str:
        """Return the preferred IP address for this node if known."""

        candidates: list[str] = []
        for value in (getattr(self, "address", "") or "",):
            value = value.strip()
            if not value:
                continue
            try:
                ipaddress.ip_address(value)
            except ValueError:
                continue
            candidates.append(value)
        for value in self.get_ipv4_addresses():
            try:
                ipaddress.ip_address(value)
            except ValueError:
                continue
            candidates.append(value)
        for value in (getattr(self, "ipv6_address", "") or "",):
            value = value.strip()
            if not value:
                continue
            try:
                ipaddress.ip_address(value)
            except ValueError:
                continue
            candidates.append(value)
        if not candidates:
            return ""
        selected = self._select_preferred_ip(candidates)
        return selected or ""

    def iter_remote_urls(self, path: str):
        """Yield potential remote URLs for ``path`` on this node."""

        host_candidates = self.get_remote_host_candidates()
        default_port = self.port or 8888
        port_candidates = [default_port]
        if self.prefers_https() and default_port not in (80, 443):
            port_candidates.insert(0, 443)
        normalized_path = path if path.startswith("/") else f"/{path}"
        seen: set[str] = set()
        scheme_candidates = self.iter_preferred_schemes()

        for host in host_candidates:
            host = host.strip()
            if not host:
                continue
            base_path = ""
            formatted_host = host
            port_override: int | None = None

            if "://" in host:
                parsed = urlparse(host)
                netloc = parsed.netloc or parsed.path
                base_path = (parsed.path or "").rstrip("/")
                combined_path = (
                    f"{base_path}{normalized_path}" if base_path else normalized_path
                )
                for scheme in self.iter_preferred_schemes(
                    default=parsed.scheme or "http"
                ):
                    candidate = urlunsplit((scheme, netloc, combined_path, "", ""))
                    if candidate not in seen:
                        seen.add(candidate)
                        yield candidate
                continue

            if host.startswith("[") and "]" in host:
                end = host.index("]")
                core_host = host[1:end]
                remainder = host[end + 1 :]
                if remainder.startswith(":"):
                    remainder = remainder[1:]
                    port_part, sep, path_tail = remainder.partition("/")
                    if port_part:
                        try:
                            port_override = int(port_part)
                        except ValueError:
                            port_override = None
                    if sep:
                        base_path = f"/{path_tail}".rstrip("/")
                elif "/" in remainder:
                    _, _, path_tail = remainder.partition("/")
                    base_path = f"/{path_tail}".rstrip("/")
                formatted_host = f"[{core_host}]"
            else:
                if "/" in host:
                    host_only, _, path_tail = host.partition("/")
                    formatted_host = host_only or host
                    base_path = f"/{path_tail}".rstrip("/")
                try:
                    ip_obj = ipaddress.ip_address(formatted_host)
                except ValueError:
                    parts = formatted_host.rsplit(":", 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        formatted_host = parts[0]
                        port_override = int(parts[1])
                    try:
                        ip_obj = ipaddress.ip_address(formatted_host)
                    except ValueError:
                        ip_obj = None
                else:
                    if ip_obj.version == 6 and not formatted_host.startswith("["):
                        formatted_host = f"[{formatted_host}]"

            combined_path = f"{base_path}{normalized_path}" if base_path else normalized_path

            for scheme in scheme_candidates:
                scheme_default_port = 443 if scheme == "https" else 80
                base = f"{scheme}://{formatted_host}"
                port_sources = (
                    [port_override] if port_override is not None else port_candidates
                )
                for candidate_port in port_sources:
                    scheme_port: int | None = candidate_port

                    if (
                        scheme_port in (80, 443)
                        and scheme_port != scheme_default_port
                    ):
                        scheme_port = None

                    if scheme_port and scheme_port != scheme_default_port:
                        explicit = f"{base}:{scheme_port}{combined_path}"
                        if explicit not in seen:
                            seen.add(explicit)
                            yield explicit

                    candidate_without_port = (
                        scheme_port is None or scheme_port == scheme_default_port
                    )
                    if candidate_without_port:
                        candidate = f"{base}{combined_path}"
                        if candidate not in seen:
                            seen.add(candidate)
                            yield candidate

    @classmethod
    def normalize_relation(cls, value):
        """Normalize ``value`` to a valid :class:`Relation`."""

        if isinstance(value, cls.Relation):
            return value
        if value is None:
            return cls.Relation.PEER
        text = str(value).strip()
        if not text:
            return cls.Relation.PEER
        for relation in cls.Relation:
            if text.lower() == relation.label.lower():
                return relation
            if text.upper() == relation.name:
                return relation
            if text.lower() == relation.value.lower():
                return relation
        return cls.Relation.PEER
