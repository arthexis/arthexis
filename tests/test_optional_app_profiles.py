from __future__ import annotations

from apps.terminals.models import AgentTerminal
from apps.users.models import ChatProfile, UserDiagnosticsProfile


def test_active_profile_models_do_not_expose_avatar_ownership() -> None:
    profile_models = (AgentTerminal, ChatProfile, UserDiagnosticsProfile)

    for model in profile_models:
        assert "avatar" not in {field.name for field in model._meta.get_fields()}
