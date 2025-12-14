from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings


_LOGGER_NAME = "register_visitor_node"
_HANDLER_ATTR = "_register_visitor_configured"
_LOCAL_LOGGER_NAME = "register_local_node"
_LOCAL_HANDLER_ATTR = "_register_local_configured"


def get_register_visitor_logger() -> logging.Logger:
    """Return a logger that writes detailed registration steps to a file."""

    logger = logging.getLogger(_LOGGER_NAME)
    if getattr(logger, _HANDLER_ATTR, False):
        return logger

    log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_dir / "register_visitor_node.log")
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    # Also propagate to the global logging configuration so entries continue to
    # show up in the default log streams during troubleshooting.
    logger.propagate = True
    setattr(logger, _HANDLER_ATTR, True)
    return logger


def get_register_local_node_logger() -> logging.Logger:
    """Return a logger for local node registration events."""

    logger = logging.getLogger(_LOCAL_LOGGER_NAME)
    if getattr(logger, _LOCAL_HANDLER_ATTR, False):
        return logger

    log_dir = Path(getattr(settings, "LOG_DIR", Path(settings.BASE_DIR) / "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_dir / "register_local_node.log")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.propagate = True
    setattr(logger, _LOCAL_HANDLER_ATTR, True)
    return logger

