from __future__ import annotations

from .models import SigilRoot

BUILTIN_SIGIL_POLICIES = {
    "CONF": {
        "context_type": SigilRoot.Context.CONFIG,
        "is_user_safe": False,
    },
    "ENV": {
        "context_type": SigilRoot.Context.CONFIG,
        "is_user_safe": False,
    },
    "REQ": {
        "context_type": SigilRoot.Context.REQUEST,
        "is_user_safe": True,
    },
    "SYS": {
        "context_type": SigilRoot.Context.CONFIG,
        "is_user_safe": False,
    },
}
