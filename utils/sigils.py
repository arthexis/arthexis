"""Utilities for resolving [SIGILS] within text using environment variables.

This module provides a small wrapper around the `sigils` package to
interpolate placeholders written as ``[NAME]`` within strings.  The
``Sigil`` class from the third-party library expects placeholders in the
form ``%[NAME]``.  To keep input strings tidy we allow users to write
``[NAME]`` and transparently convert them before interpolation.

Example
-------
>>> import os
>>> from utils.sigils import resolve_env_sigils
>>> os.environ['PORT'] = '8000'
>>> resolve_env_sigils('Running on port [PORT]')
'Running on port 8000'

The function defaults to using ``os.environ`` as the context, but any
mapping can be supplied if desired.
"""
from __future__ import annotations

import os
import re
from typing import Mapping, Optional

from sigils import Sigil

# Regular expression used to detect ``[SIGIL]`` style placeholders.
_SIGIL_PATTERN = re.compile(r"\[([^\[\]]+)\]")


def _convert_to_sigils(template: str) -> str:
    """Convert ``[name]`` placeholders into the ``sigils`` format ``%[name]``."""
    # Replace each ``[something]`` with ``%[something]`` which the
    # ``Sigil`` class recognises.
    return _SIGIL_PATTERN.sub(r"%[\1]", template)


def resolve_env_sigils(text: str, env: Optional[Mapping[str, str]] = None) -> str:
    """Resolve ``[SIGILS]`` in ``text`` using values from ``env``.

    Parameters
    ----------
    text:
        The input string that may contain ``[NAME]`` placeholders.
    env:
        Optional mapping providing values for placeholders.  Defaults to
        :data:`os.environ`.

    Returns
    -------
    str
        The text with all placeholders replaced.  Placeholders without a
        matching key remain unchanged.
    """
    if not isinstance(text, str) or "[" not in text:
        return text

    env = dict(os.environ if env is None else env)
    template = _convert_to_sigils(text)
    return Sigil(template).interpolate(env, handle_errors="ignore")
