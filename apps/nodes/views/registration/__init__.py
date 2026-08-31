"""Registration views package."""

# Compatibility export for legacy monkeypatching in external tests.
import socket

from .auth import (
    _authenticate_basic_credentials,
    _enforce_authentication,
    _verify_signature,
)
from .cors import add_cors_headers as _add_cors_headers
from .handlers import (
    next_gway_number,
    node_info,
    node_list,
    register_node,
    register_visitor_proxy,
    register_visitor_telemetry,
    submit_enrollment_public_key,
)
from .network import (
    _get_host_domain,
    _get_host_ip,
    _get_host_port,
)
from .network import (
    append_token as _append_token,
)
from .network import (
    get_advertised_address as _get_advertised_address,
)
from .network import (
    get_client_ip as _get_client_ip,
)
from .network import (
    get_public_targets as _get_public_targets,
)
from .network import (
    iter_port_fallback_urls as _iter_port_fallback_urls,
)
from .network_utils import _get_route_address
from .payload import NodeRegistrationPayload
from .policy import get_allowed_visitor_suffixes as _get_allowed_visitor_suffixes
from .policy import is_allowed_visitor_url as _is_allowed_visitor_url
from .sanitization import (
    redact_mac as _redact_mac,
)
from .sanitization import (
    redact_network_value as _redact_network_value,
)
from .sanitization import (
    redact_token_value as _redact_token_value,
)
from .sanitization import (
    redact_url_token as _redact_url_token,
)
from .sanitization import (
    redact_value as _redact_value,
)

__all__ = [
    "NodeRegistrationPayload",
    "_add_cors_headers",
    "_append_token",
    "_authenticate_basic_credentials",
    "_enforce_authentication",
    "_get_advertised_address",
    "_get_allowed_visitor_suffixes",
    "_get_client_ip",
    "_get_host_domain",
    "_get_host_ip",
    "_get_host_port",
    "_get_public_targets",
    "_get_route_address",
    "_is_allowed_visitor_url",
    "_iter_port_fallback_urls",
    "_redact_mac",
    "_redact_network_value",
    "_redact_token_value",
    "_redact_url_token",
    "_redact_value",
    "_verify_signature",
    "node_info",
    "node_list",
    "next_gway_number",
    "register_node",
    "register_visitor_proxy",
    "register_visitor_telemetry",
    "submit_enrollment_public_key",
]
