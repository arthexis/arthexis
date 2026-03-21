"""Regression coverage for shortcut execution user flows."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.features.models import Feature
from apps.shortcuts.constants import SHORTCUT_MANAGEMENT_FEATURE_SLUG
from apps.shortcuts.models import ClipboardPattern, Shortcut, ShortcutTargetKind
from apps.shortcuts.runtime import execute_server_shortcut


@pytest.mark.django_db
def test_client_shortcut_executes_first_matching_clipboard_pattern(client) -> None:
    """Client shortcuts should execute the first matching clipboard pattern target."""

    feature, _ = Feature.objects.update_or_create(
        slug=SHORTCUT_MANAGEMENT_FEATURE_SLUG,
        defaults={"display": "Shortcut Management", "is_enabled": True},
    )
    assert feature.is_enabled

    user_model = get_user_model()
    user = user_model.objects.create_user(username="shortcut-user", password="password", is_staff=True)
    client.force_login(user)

    shortcut = Shortcut.objects.create(
        display="Clipboard shortcut",
        key_combo="CTRL+SHIFT+V",
        kind=Shortcut.Kind.CLIENT,
        target_kind=ShortcutTargetKind.COMMAND,
        target_identifier="text.append_suffix",
        target_payload={"source": "clipboard", "suffix": "-fallback"},
        use_clipboard_patterns=True,
        is_active=True,
        clipboard_output_enabled=True,
    )
    ClipboardPattern.objects.create(
        shortcut=shortcut,
        display="ticket",
        pattern=r"^TKT-",
        priority=1,
        target_kind=ShortcutTargetKind.COMMAND,
        target_identifier="text.append_suffix",
        target_payload={"source": "clipboard", "suffix": "-pattern"},
        is_active=True,
        clipboard_output_enabled=True,
    )

    response = client.post(
        reverse("shortcuts:client-execute", args=[shortcut.pk]),
        data=json.dumps({"clipboard": "TKT-10"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["target_identifier"] == "text.append_suffix"
    assert payload["action_result"]["value"] == "TKT-10-pattern"
    assert payload["clipboard_output"] == "TKT-10-pattern"
    assert payload["matched_pattern_id"] is not None


@pytest.mark.django_db
def test_client_shortcut_workflow_output_template_uses_action_payload(client) -> None:
    """Client shortcuts should render output from typed action-result payloads."""

    Feature.objects.update_or_create(
        slug=SHORTCUT_MANAGEMENT_FEATURE_SLUG,
        defaults={"display": "Shortcut Management", "is_enabled": True},
    )
    user_model = get_user_model()
    user = user_model.objects.create_user(username="workflow-user", password="password", is_staff=True)
    client.force_login(user)

    shortcut = Shortcut.objects.create(
        display="Template shortcut",
        key_combo="CTRL+SHIFT+T",
        kind=Shortcut.Kind.CLIENT,
        target_kind=ShortcutTargetKind.WORKFLOW,
        target_identifier="text.render_template",
        target_payload={"template": "[ARG.clipboard]-wf"},
        is_active=True,
        clipboard_output_enabled=True,
        output_template="[ARG.action_result]!",
    )

    response = client.post(
        reverse("shortcuts:client-execute", args=[shortcut.pk]),
        data=json.dumps({"clipboard": "ABC"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action_result"]["value"] == "ABC-wf"
    assert payload["clipboard_output"] == "ABC-wf!"


@pytest.mark.django_db
def test_server_shortcut_executes_typed_target() -> None:
    """Server shortcuts should execute structured command targets without recipes."""

    shortcut = Shortcut.objects.create(
        display="Server shortcut",
        key_combo="CTRL+ALT+S",
        kind=Shortcut.Kind.SERVER,
        target_kind=ShortcutTargetKind.COMMAND,
        target_identifier="text.prepend_prefix",
        target_payload={"source": "shortcut_key", "prefix": "run:"},
        is_active=True,
    )

    execution = execute_server_shortcut(shortcut=shortcut)

    assert execution.target_identifier == "text.prepend_prefix"
    assert execution.action_result.value == "run:CTRL+ALT+S"
