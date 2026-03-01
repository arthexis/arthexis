"""Tests for taskbar menu and action model validation."""

from __future__ import annotations

import pytest

from django.core.exceptions import ValidationError

from apps.taskbar.models import TaskbarMenu, TaskbarMenuAction


@pytest.mark.django_db
def test_command_action_requires_command_value():
    """Command action type should require a command payload."""

    menu = TaskbarMenu.objects.create(name="Main", slug="main")
    action = TaskbarMenuAction(
        menu=menu,
        label="Open",
        action_type=TaskbarMenuAction.ActionType.COMMAND,
        command="",
    )

    with pytest.raises(ValidationError):
        action.full_clean()
