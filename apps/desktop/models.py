"""Models for desktop assistant extension registration and desktop shortcuts."""

from __future__ import annotations

import ast
import shlex
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import models

from apps.base.models import Entity


_ALLOWED_CONDITION_AST_NODES = (
    ast.And,
    ast.BoolOp,
    ast.Call,
    ast.Compare,
    ast.Constant,
    ast.Eq,
    ast.Expression,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.List,
    ast.Load,
    ast.Lt,
    ast.LtE,
    ast.Name,
    ast.Not,
    ast.NotEq,
    ast.NotIn,
    ast.Or,
    ast.Set,
    ast.Tuple,
    ast.UnaryOp,
)
_ALLOWED_CONDITION_NAMES = {
    "group_names",
    "has_desktop_ui",
    "has_feature",
    "is_staff",
    "is_superuser",
}
_ALLOWED_URL_SCHEMES = {"http", "https"}


def _is_has_feature_callable_name(node: ast.Name, parents: dict[ast.AST, ast.AST]) -> bool:
    """Return whether ``node`` is the callable name in a ``has_feature(...)`` call.

    Parameters:
        node: The AST name node under inspection.
        parents: Mapping of child nodes to their direct parent node.

    Returns:
        ``True`` when the name is used as the function target for a call.
    """

    parent = parents.get(node)
    return isinstance(parent, ast.Call) and parent.func is node


class RegisteredExtension(Entity):
    """Map a file extension to a Django management command executable."""

    extension = models.CharField(
        max_length=32,
        unique=True,
        help_text="Operating system extension including dot (for example: .pdf).",
    )
    description = models.TextField(
        blank=True,
        help_text=(
            "Administrative notes. Use filename sigil in command args to inject "
            "the opened file path."
        ),
    )
    django_command = models.CharField(
        max_length=128,
        help_text="Django management command to execute.",
    )
    extra_args = models.TextField(
        blank=True,
        help_text=(
            "Optional arguments (shell-style) for the command. The filename sigil "
            "will be replaced with the opened file path."
        ),
    )
    filename_sigil = models.CharField(
        max_length=64,
        default="{filename}",
        help_text="Sigil token replaced with the opened file path in extra args.",
    )
    filename_as_input = models.BooleanField(
        default=False,
        help_text=(
            "Pass the opened file path as command stdin instead of replacing the "
            "filename sigil."
        ),
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("extension",)
        verbose_name = "Registered Extension"
        verbose_name_plural = "Registered Extensions"

    def __str__(self) -> str:
        """Return the registered file extension."""
        return self.extension

    def clean(self) -> None:
        """Validate extension and execution settings."""
        super().clean()

        if not self.extension.startswith("."):
            raise ValidationError({"extension": "Extension must start with a dot."})

        if any(char in self.extension for char in (" ", "/", "\\")):
            raise ValidationError(
                {
                    "extension": (
                        "Extension cannot include spaces or path separators."
                    )
                }
            )

        if not self.django_command.strip():
            raise ValidationError({"django_command": "Command name is required."})

        if not self.filename_as_input and not self.filename_sigil.strip():
            raise ValidationError(
                {"filename_sigil": "Filename sigil cannot be empty."}
            )

    def build_runtime_command(self, filename: str | None = None) -> tuple[list[str], str | None]:
        """Build command arguments and optional input payload for execution.

        Parameters:
            filename: Optional file path selected by the operating system.

        Returns:
            A tuple containing the management-command argument vector and optional
            stdin payload.
        """

        parsed_args = shlex.split(self.extra_args) if self.extra_args else []
        if filename and not self.filename_as_input:
            parsed_args = [arg.replace(self.filename_sigil, filename) for arg in parsed_args]
        input_data = filename if (filename and self.filename_as_input) else None
        return [self.django_command, *parsed_args], input_data


class DesktopShortcut(Entity):
    """Represent an OS desktop shortcut that can be synchronized from Django data."""

    class LaunchMode(models.TextChoices):
        """Available target launch modes for desktop shortcuts."""

        URL = "url", "URL"

    class InstallLocation(models.TextChoices):
        """Filesystem locations where launcher files should be written."""

        DESKTOP = "desktop", "Desktop"
        APPLICATIONS = "applications", "Applications menu"
        BOTH = "both", "Desktop and Applications menu"

    slug = models.SlugField(max_length=80, unique=True)
    desktop_filename = models.CharField(
        max_length=128,
        unique=True,
        help_text="Filename used in the desktop folder (without .desktop suffix).",
    )
    name = models.CharField(max_length=128)
    comment = models.CharField(max_length=255, blank=True)
    launch_mode = models.CharField(
        max_length=16,
        choices=LaunchMode.choices,
        default=LaunchMode.URL,
        help_text="Desktop shortcuts always open a URL through the browser helper.",
    )
    target_url = models.CharField(
        max_length=512,
        blank=True,
        help_text="HTTP or HTTPS URL to open. Supports the {port} placeholder.",
    )
    install_location = models.CharField(
        max_length=16,
        choices=InstallLocation.choices,
        default=InstallLocation.DESKTOP,
        help_text="Where the .desktop launcher file should be installed.",
    )
    icon_name = models.CharField(
        max_length=128,
        blank=True,
        help_text="Existing OS icon name to reference (for example web-browser).",
    )
    icon_base64 = models.TextField(
        blank=True,
        help_text="Base64 encoded icon payload written to ~/.local/share/icons.",
    )
    icon_extension = models.CharField(
        max_length=8,
        default="png",
        help_text="Extension used when persisting base64 icon payloads.",
    )
    categories = models.CharField(max_length=255, blank=True, default="")
    terminal = models.BooleanField(default=False)
    startup_notify = models.BooleanField(default=True)
    extra_entries = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional Desktop Entry key/value pairs.",
    )
    condition_expression = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Optional constrained boolean expression evaluated against context "
            "keys: has_desktop_ui, has_feature, is_staff, is_superuser, "
            "group_names."
        ),
    )
    require_desktop_ui = models.BooleanField(default=True)
    required_features = models.ManyToManyField("nodes.NodeFeature", blank=True)
    required_groups = models.ManyToManyField("auth.Group", blank=True)
    only_staff = models.BooleanField(default=False)
    only_superuser = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ("sort_order", "name", "slug")
        verbose_name = "Desktop Shortcut"
        verbose_name_plural = "Desktop Shortcuts"

    def __str__(self) -> str:
        """Return a readable label for admin interfaces."""
        return self.name

    @staticmethod
    def validate_condition_expression(expression: str) -> None:
        """Validate the constrained condition expression syntax.

        Parameters:
            expression: The expression entered by an administrator.

        Raises:
            ValidationError: If the expression uses unsupported syntax or names.
        """

        if not expression:
            return

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValidationError(
                "Enter a valid condition expression."
            ) from exc

        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_CONDITION_AST_NODES):
                raise ValidationError(
                    "Condition expressions may only use booleans, comparisons, collections, and has_feature(...)."
                )
            if isinstance(node, ast.Name) and node.id not in _ALLOWED_CONDITION_NAMES:
                raise ValidationError(
                    f"Condition expressions cannot reference '{node.id}'."
                )
            if (
                isinstance(node, ast.Name)
                and node.id == "has_feature"
                and not _is_has_feature_callable_name(node, parents)
            ):
                raise ValidationError(
                    "Condition expressions must call has_feature(...)."
                )
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id != "has_feature":
                    raise ValidationError(
                        "Condition expressions may only call has_feature(...)."
                    )

    @staticmethod
    def validate_target_url(target_url: str) -> None:
        """Validate that ``target_url`` is an HTTP(S) URL.

        Parameters:
            target_url: The raw URL template stored on the model.

        Raises:
            ValidationError: If the URL is blank or uses an unsafe scheme.
        """

        normalized_target_url = target_url.strip()
        if not normalized_target_url:
            raise ValidationError("A target URL is required.")

        try:
            parsed = urlparse(normalized_target_url.format(port="80"))
        except (KeyError, ValueError) as exc:
            raise ValidationError(
                "Target URL must only use the {port} placeholder."
            ) from exc
        if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
            raise ValidationError(
                "Target URL must be an absolute http:// or https:// URL."
            )

    def clean(self) -> None:
        """Validate desktop shortcut launch and icon configuration."""
        super().clean()

        errors: dict[str, list[str] | str] = {}

        if self.launch_mode != self.LaunchMode.URL:
            errors["launch_mode"] = "Desktop shortcuts must use URL launch mode."

        try:
            self.validate_target_url(self.target_url)
        except ValidationError as exc:
            errors["target_url"] = exc.messages

        try:
            self.validate_condition_expression(self.condition_expression)
        except ValidationError as exc:
            errors["condition_expression"] = exc.messages

        if self.icon_base64 and self.icon_name:
            errors["icon_base64"] = "Choose either icon base64 payload or icon name, not both."
        if not self.desktop_filename.strip():
            errors["desktop_filename"] = "Desktop filename is required."
        if "/" in self.desktop_filename or "\\" in self.desktop_filename:
            errors["desktop_filename"] = "Desktop filename cannot contain path separators."

        if errors:
            raise ValidationError(errors)


__all__ = ["DesktopShortcut", "RegisteredExtension"]
