from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings


_LOGGER_NAME = "register_visitor_node"
_HANDLER_ATTR = "_register_visitor_configured"
_LOCAL_LOGGER_NAME = "register_local_node"
_LOCAL_HANDLER_ATTR = "_register_local_configured"


def _build_registration_logger(
    name: str, attr_flag: str, filename: str
) -> logging.Logger:
    """Create or retrieve a configured registration logger."""

    logger = logging.getLogger(name)
    if getattr(logger, attr_flag, False):
        return logger

    log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_dir / filename)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    # Also propagate to the global logging configuration so entries continue to
    # show up in the default log streams during troubleshooting.
    logger.propagate = True
    setattr(logger, attr_flag, True)
    return logger


def get_register_visitor_logger() -> logging.Logger:
    """Return a logger that writes detailed registration steps to a file."""

    return _build_registration_logger(
        _LOGGER_NAME, _HANDLER_ATTR, "register_visitor_node.log"
    )


def get_register_local_node_logger() -> logging.Logger:
    """Return a logger for local node registration events."""

    return _build_registration_logger(
        _LOCAL_LOGGER_NAME, _LOCAL_HANDLER_ATTR, "register_local_node.log"
    )

