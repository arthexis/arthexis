import base64
import logging
from datetime import timedelta
from pathlib import Path

from django.db import models
from django.db.models import Q
from core.entity import Entity
from core.models import Lead, SecurityGroup
from django.contrib.sites.models import Site
from nodes.models import ContentSample, NodeRole
from django.apps import apps as django_apps
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext, gettext_lazy as _, get_language_info
from importlib import import_module
from django.urls import URLPattern
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator
from django.core.exceptions import ValidationError

from core import github_issues
from .tasks import create_user_story_github_issue


logger = logging.getLogger(__name__)


class ApplicationManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class Application(Entity):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    objects = ApplicationManager()

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    @property
    def installed(self) -> bool:
        return django_apps.is_installed(self.name)

    @property
    def verbose_name(self) -> str:
        try:
            return django_apps.get_app_config(self.name).verbose_name
        except LookupError:
            return self.name


    class Meta:
        verbose_name = _("Application")
        verbose_name_plural = _("Applications")


class ModuleManager(models.Manager):
    def get_by_natural_key(self, role: str, path: str):
        return self.get(node_role__name=role, path=path)


class Module(Entity):
    node_role = models.ForeignKey(
        NodeRole,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    path = models.CharField(
        max_length=100,
        help_text="Base path for the app, starting with /",
        blank=True,
    )
    menu = models.CharField(
        max_length=100,
        blank=True,
        help_text="Text used for the navbar pill; defaults to the application name.",
    )
    is_default = models.BooleanField(default=False)
    favicon = models.ImageField(upload_to="modules/favicons/", blank=True)

    objects = ModuleManager()

    class Meta:
        verbose_name = _("Module")
        verbose_name_plural = _("Modules")
        unique_together = ("node_role", "path")

    def natural_key(self):  # pragma: no cover - simple representation
        role_name = None
        if getattr(self, "node_role_id", None):
            role_name = self.node_role.name
        return (role_name, self.path)

    natural_key.dependencies = ["nodes.NodeRole"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.application.name} ({self.path})"

    @property
    def menu_label(self) -> str:
        return self.menu or self.application.name

    def save(self, *args, **kwargs):
        if not self.path:
            self.path = f"/{slugify(self.application.name)}/"
        super().save(*args, **kwargs)

    def create_landings(self):
        try:
            urlconf = import_module(f"{self.application.name}.urls")
        except Exception:
            try:
                urlconf = import_module(f"{self.application.name.lower()}.urls")
            except Exception:
                Landing.objects.get_or_create(
                    module=self,
                    path=self.path,
                    defaults={"label": self.application.name},
                )
                return
        patterns = getattr(urlconf, "urlpatterns", [])
        created = False
        normalized_module = self.path.strip("/")

        def _walk(patterns, prefix=""):
            nonlocal created
            for pattern in patterns:
                if isinstance(pattern, URLPattern):
                    callback = pattern.callback
                    if getattr(callback, "landing", False):
                        pattern_path = str(pattern.pattern)
                        relative = f"{prefix}{pattern_path}"
                        if normalized_module and relative.startswith(normalized_module):
                            full_path = f"/{relative}"
                            Landing.objects.update_or_create(
                                module=self,
                                path=full_path,
                                defaults={
                                    "label": getattr(
                                        callback,
                                        "landing_label",
                                        callback.__name__.replace("_", " ").title(),
                                    )
                                },
                            )
                        else:
                            full_path = f"{self.path}{relative}"
                            Landing.objects.get_or_create(
                                module=self,
                                path=full_path,
                                defaults={
                                    "label": getattr(
                                        callback,
                                        "landing_label",
                                        callback.__name__.replace("_", " ").title(),
                                    )
                                },
                            )
                        created = True
                else:
                    _walk(
                        pattern.url_patterns, prefix=f"{prefix}{str(pattern.pattern)}"
                    )

        _walk(patterns)

        if not created:
            Landing.objects.get_or_create(
                module=self, path=self.path, defaults={"label": self.application.name}
            )


class SiteBadge(Entity):
    site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="badge")
    badge_color = models.CharField(max_length=7, default="#28a745")
    favicon = models.ImageField(upload_to="sites/favicons/", blank=True)
    landing_override = models.ForeignKey(
        "Landing", null=True, blank=True, on_delete=models.SET_NULL
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Badge for {self.site.domain}"

    class Meta:
        verbose_name = "Site Badge"
        verbose_name_plural = "Site Badges"


class SiteProxy(Site):
    class Meta:
        proxy = True
        app_label = "pages"
        verbose_name = "Site"
        verbose_name_plural = "Sites"


class LandingManager(models.Manager):
    def get_by_natural_key(self, role: str, module_path: str, path: str):
        return self.get(
            module__node_role__name=role, module__path=module_path, path=path
        )


class Landing(Entity):
    module = models.ForeignKey(
        Module, on_delete=models.CASCADE, related_name="landings"
    )
    path = models.CharField(max_length=200)
    label = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    track_leads = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    objects = LandingManager()

    class Meta:
        unique_together = ("module", "path")
        verbose_name = _("Landing")
        verbose_name_plural = _("Landings")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.label} ({self.path})"

    def save(self, *args, **kwargs):
        existing = None
        if not self.pk:
            existing = (
                type(self).objects.filter(module=self.module, path=self.path).first()
            )
        if existing:
            self.pk = existing.pk
        super().save(*args, **kwargs)


class LandingLead(Lead):
    landing = models.ForeignKey(
        "pages.Landing", on_delete=models.CASCADE, related_name="leads"
    )

    class Meta:
        verbose_name = _("Landing Lead")
        verbose_name_plural = _("Landing Leads")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.landing.label} ({self.path})"


class RoleLandingManager(models.Manager):
    def get_by_natural_key(
        self,
        role: str | None,
        group: str | None,
        username: str | None,
        module_path: str,
        path: str,
    ):
        filters = {
            "landing__module__path": module_path,
            "landing__path": path,
        }
        if role:
            filters["node_role__name"] = role
        else:
            filters["node_role__isnull"] = True
        if group:
            filters["security_group__name"] = group
        else:
            filters["security_group__isnull"] = True
        if username:
            filters["user__username"] = username
        else:
            filters["user__isnull"] = True
        return self.get(**filters)


class RoleLanding(Entity):
    node_role = models.OneToOneField(
        NodeRole,
        on_delete=models.CASCADE,
        related_name="default_landing",
        null=True,
        blank=True,
    )
    security_group = models.OneToOneField(
        SecurityGroup,
        on_delete=models.CASCADE,
        related_name="default_landing",
        null=True,
        blank=True,
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="default_landing",
        null=True,
        blank=True,
    )
    landing = models.ForeignKey(
        Landing,
        on_delete=models.CASCADE,
        related_name="role_defaults",
    )
    priority = models.IntegerField(default=0)

    objects = RoleLandingManager()

    class Meta:
        verbose_name = _("Default Landing")
        verbose_name_plural = _("Default Landings")
        ordering = ("-priority", "pk")
        constraints = [
            models.CheckConstraint(
                name="pages_rolelanding_single_target",
                condition=(
                    Q(
                        node_role__isnull=False,
                        security_group__isnull=True,
                        user__isnull=True,
                    )
                    | Q(
                        node_role__isnull=True,
                        security_group__isnull=False,
                        user__isnull=True,
                    )
                    | Q(
                        node_role__isnull=True,
                        security_group__isnull=True,
                        user__isnull=False,
                    )
                ),
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.node_role_id:
            role_name = self.node_role.name
        elif self.security_group_id:
            role_name = self.security_group.name
        elif self.user_id:
            role_name = self.user.get_username()
        else:  # pragma: no cover - guarded by constraint
            role_name = "?"
        landing_path = self.landing.path if self.landing_id else "?"
        return f"{role_name} → {landing_path}"

    def natural_key(self):  # pragma: no cover - simple representation
        role_name = None
        group_name = None
        username = None
        if getattr(self, "node_role_id", None):
            role_name = self.node_role.name
        if getattr(self, "security_group_id", None):
            group_name = self.security_group.name
        if getattr(self, "user_id", None):
            username = self.user.get_username()
        landing_key = (None, None)
        if getattr(self, "landing_id", None):
            landing_key = (
                self.landing.module.path if self.landing.module_id else None,
                self.landing.path,
            )
        return (role_name, group_name, username) + landing_key

    natural_key.dependencies = [
        "nodes.NodeRole",
        "core.SecurityGroup",
        settings.AUTH_USER_MODEL,
        "pages.Landing",
    ]

    def clean(self):
        super().clean()
        targets = [
            bool(self.node_role_id),
            bool(self.security_group_id),
            bool(self.user_id),
        ]
        if sum(targets) == 0:
            raise ValidationError(
                {
                    "node_role": _("Select a node role, security group, or user."),
                    "security_group": _(
                        "Select a node role, security group, or user."
                    ),
                    "user": _("Select a node role, security group, or user."),
                }
            )
        if sum(targets) > 1:
            raise ValidationError(
                {
                    "node_role": _(
                        "Only one of node role, security group, or user may be set."
                    ),
                    "security_group": _(
                        "Only one of node role, security group, or user may be set."
                    ),
                    "user": _(
                        "Only one of node role, security group, or user may be set."
                    ),
                }
            )

class UserManual(Entity):
    class PdfOrientation(models.TextChoices):
        LANDSCAPE = "landscape", _("Landscape")
        PORTRAIT = "portrait", _("Portrait")

    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=200)
    languages = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Comma-separated 2-letter language codes",
    )
    content_html = models.TextField()
    content_pdf = models.TextField(help_text="Base64 encoded PDF")
    pdf_orientation = models.CharField(
        max_length=10,
        choices=PdfOrientation.choices,
        default=PdfOrientation.LANDSCAPE,
        help_text=_("Orientation used when rendering the PDF download."),
    )

    class Meta:
        db_table = "man_usermanual"
        verbose_name = "User Manual"
        verbose_name_plural = "User Manuals"

    def __str__(self):  # pragma: no cover - simple representation
        return self.title

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.slug,)

    def _ensure_pdf_is_base64(self) -> None:
        """Normalize ``content_pdf`` so stored values are base64 strings."""

        value = self.content_pdf
        if value in {None, ""}:
            self.content_pdf = "" if value is None else value
            return

        if isinstance(value, (bytes, bytearray, memoryview)):
            self.content_pdf = base64.b64encode(bytes(value)).decode("ascii")
            return

        reader = getattr(value, "read", None)
        if callable(reader):
            data = reader()
            if hasattr(value, "seek"):
                try:
                    value.seek(0)
                except Exception:  # pragma: no cover - best effort reset
                    pass
            self.content_pdf = base64.b64encode(data).decode("ascii")
            return

        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("data:"):
                _, _, encoded = stripped.partition(",")
                self.content_pdf = encoded.strip()

    def save(self, *args, **kwargs):
        self._ensure_pdf_is_base64()
        super().save(*args, **kwargs)


class ViewHistory(Entity):
    """Record of public site visits."""

    path = models.CharField(max_length=500)
    method = models.CharField(max_length=10)
    status_code = models.PositiveSmallIntegerField()
    status_text = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    view_name = models.CharField(max_length=200, blank=True)
    visited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-visited_at"]
        verbose_name = _("View History")
        verbose_name_plural = _("View Histories")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.method} {self.path} ({self.status_code})"

    @classmethod
    def purge_older_than(cls, *, days: int) -> int:
        """Delete history entries recorded more than ``days`` days ago."""

        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = cls.objects.filter(visited_at__lt=cutoff).delete()
        return deleted


class Favorite(Entity):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorites",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    custom_label = models.CharField(max_length=100, blank=True)
    user_data = models.BooleanField(default=False)
    priority = models.IntegerField(default=0)

    class Meta:
        unique_together = ("user", "content_type")
        ordering = ["priority", "pk"]
        verbose_name = _("Favorite")
        verbose_name_plural = _("Favorites")


class UserStory(Lead):
    path = models.CharField(max_length=500)
    name = models.CharField(max_length=40, blank=True)
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text=_("Rate your experience from 1 (lowest) to 5 (highest)."),
    )
    comments = models.TextField(
        validators=[MaxLengthValidator(400)],
        help_text=_("Share more about your experience."),
    )
    take_screenshot = models.BooleanField(
        default=True,
        help_text=_("Request a screenshot capture for this feedback."),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="user_stories",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="owned_user_stories",
        help_text=_("Internal owner for this feedback."),
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    github_issue_number = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text=_("Number of the GitHub issue created for this feedback."),
    )
    github_issue_url = models.URLField(
        blank=True,
        help_text=_("Link to the GitHub issue created for this feedback."),
    )
    screenshot = models.ForeignKey(
        ContentSample,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="user_stories",
        help_text=_("Screenshot captured for this feedback."),
    )
    language_code = models.CharField(
        max_length=15,
        blank=True,
        help_text=_("Language selected when the feedback was submitted."),
    )

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = _("User Story")
        verbose_name_plural = _("User Stories")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        display = self.name or _("Anonymous")
        return f"{display} ({self.rating}/5)"

    def get_github_issue_labels(self) -> list[str]:
        """Return default labels used when creating GitHub issues."""

        return ["feedback"]

    def get_github_issue_fingerprint(self) -> str | None:
        """Return a fingerprint used to avoid duplicate issue submissions."""

        if self.pk:
            return f"user-story:{self.pk}"
        return None

    def build_github_issue_title(self) -> str:
        """Return the title used for GitHub issues."""

        path = self.path or "/"
        return gettext("Feedback for %(path)s (%(rating)s/5)") % {
            "path": path,
            "rating": self.rating,
        }

    def build_github_issue_body(self) -> str:
        """Return the issue body summarising the feedback details."""

        name = self.name or gettext("Anonymous")
        path = self.path or "/"
        screenshot_requested = gettext("Yes") if self.take_screenshot else gettext("No")

        lines = [
            f"**Path:** {path}",
            f"**Rating:** {self.rating}/5",
            f"**Name:** {name}",
            f"**Screenshot requested:** {screenshot_requested}",
        ]

        language_code = (self.language_code or "").strip()
        if language_code:
            normalized = language_code.replace("_", "-").lower()
            try:
                info = get_language_info(normalized)
            except KeyError:
                language_display = ""
            else:
                language_display = info.get("name_local") or info.get("name") or ""

            if language_display:
                lines.append(f"**Language:** {language_display} ({normalized})")
            else:
                lines.append(f"**Language:** {normalized}")

        if self.submitted_at:
            lines.append(f"**Submitted at:** {self.submitted_at.isoformat()}")

        comment = (self.comments or "").strip()
        if comment:
            lines.extend(["", comment])

        return "\n".join(lines).strip()

    def create_github_issue(self) -> str | None:
        """Create a GitHub issue for this feedback and store the identifiers."""

        if self.github_issue_url:
            return self.github_issue_url

        response = github_issues.create_issue(
            self.build_github_issue_title(),
            self.build_github_issue_body(),
            labels=self.get_github_issue_labels(),
            fingerprint=self.get_github_issue_fingerprint(),
        )

        if response is None:
            return None

        try:
            payload = response.json()
        except ValueError:  # pragma: no cover - defensive guard
            payload = {}

        issue_url = payload.get("html_url")
        issue_number = payload.get("number")

        update_fields = []
        if issue_url and issue_url != self.github_issue_url:
            self.github_issue_url = issue_url
            update_fields.append("github_issue_url")
        if issue_number is not None and issue_number != self.github_issue_number:
            self.github_issue_number = issue_number
            update_fields.append("github_issue_number")

        if update_fields:
            self.save(update_fields=update_fields)

        return issue_url


from django.db.models.signals import post_save
from django.dispatch import receiver


def _celery_lock_path() -> Path:
    return Path(settings.BASE_DIR) / "locks" / "celery.lck"


def _is_celery_enabled() -> bool:
    return _celery_lock_path().exists()


@receiver(post_save, sender=UserStory)
def _queue_low_rating_user_story_issue(
    sender, instance: UserStory, created: bool, raw: bool, **kwargs
) -> None:
    if raw or not created:
        return
    if instance.rating >= 5:
        return
    if instance.github_issue_url:
        return
    if not instance.user_id:
        return
    if not _is_celery_enabled():
        return

    try:
        create_user_story_github_issue.delay(instance.pk)
    except Exception:  # pragma: no cover - logging only
        logger.exception(
            "Failed to enqueue GitHub issue creation for user story %s", instance.pk
        )


@receiver(post_save, sender=Module)
def _create_landings(
    sender, instance, created, raw, **kwargs
):  # pragma: no cover - simple handler
    if created and not raw:
        instance.create_landings()
