from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from config.middleware import ActiveAppMiddleware
from config.request_utils import get_request_log_context, reset_request_log_context, set_request_log_context
from utils.loggers.filters import RequestContextFilter
from utils.loggers.json_formatter import JSONFormatter

def _build_request(**kwargs):
    resolver_match = kwargs.pop("resolver_match", None)
    return SimpleNamespace(
        META=kwargs.pop("META", {}),
        GET=kwargs.pop("GET", {}),
        POST=kwargs.pop("POST", {}),
        resolver_match=resolver_match,
        **kwargs,
    )

