from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.nodes.models import NodeRole
from apps.sigils.sigil_resolver import resolve_sigils
from apps.users.models.profile import Profile


class AgentTerminal(Profile):
    """Terminal process definition assigned to users, groups, or avatars."""

    owner_required = True

    LOOP_END = "end"
    LOOP_REPEAT = "loop"
    LOOP_CHOICES = ((LOOP_END, _("End")), (LOOP_REPEAT, _("Loop")))

    name = models.CharField(max_length=120, unique=True)
    node_role = models.ForeignKey(
        NodeRole,
        on_delete=models.PROTECT,
        related_name="agent_terminals",
        null=True,
        blank=True,
        help_text=_("Node role this terminal definition applies to. Defaults to Terminal role."),
    )
    executable = models.CharField(max_length=255, blank=True, help_text=_("Optional executable command."))
    launch_command = models.TextField(blank=True, help_text=_("Command to send after launch."))
    launch_prompt = models.TextField(blank=True, help_text=_("Prompt text to send after command."))
    prompt_blocks = models.JSONField(default=list, blank=True)
    auto_close_on_exit = models.BooleanField(default=False)
    prompt_block_mode = models.CharField(max_length=8, choices=LOOP_CHOICES, default=LOOP_END)
    startup_maximized = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "terminals"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()
        if self.prompt_blocks and not isinstance(self.prompt_blocks, list):
            raise ValidationError({"prompt_blocks": _("Prompt blocks must be a list.")})

    def resolved_executable(self) -> str:
        return resolve_sigils((self.executable or "").strip(), current=None) or "x-terminal-emulator"

    def resolved_launch_command(self) -> str:
        return resolve_sigils(self.launch_command or "", current=None)

    def resolved_launch_prompt(self) -> str:
        return resolve_sigils(self.launch_prompt or "", current=None)

    def resolved_prompt_blocks(self) -> list[str]:
        resolved: list[str] = []
        for block in self.prompt_blocks or []:
            if isinstance(block, dict):
                text = str(block.get("prompt", ""))
            else:
                text = str(block)
            resolved.append(resolve_sigils(text, current=None))
        return resolved

    def effective_node_role(self):
        if self.node_role_id:
            return self.node_role
        return NodeRole.objects.filter(name="Terminal").first()

    @classmethod
    def assigned_to_any_user(cls):
        return cls.objects.filter(
            Q(user__isnull=False)
            | Q(group__user__isnull=False)
            | Q(avatar__user__isnull=False)
            | Q(avatar__group__user__isnull=False)
        ).distinct()
