from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import Ownable
from apps.groups.models import SecurityGroup
from apps.sigils.fields import SigilShortAutoField

from .package import Package


def _default_expiration():
    return timezone.now() + timedelta(days=30)


class GithubToken(Ownable):
    """Store scoped GitHub tokens for package releases."""

    owner_required = False

    token = SigilShortAutoField(
        max_length=255,
        verbose_name=_("GitHub token"),
        help_text=_("Personal access token used for GitHub release operations."),
    )
    package = models.ForeignKey(
        Package,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="github_tokens",
        help_text=_("Optional package scope. Leave blank to apply to any package."),
    )
    expires_at = models.DateTimeField(
        default=_default_expiration,
        help_text=_("When this token expires."),
    )
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "GitHub Token"
        verbose_name_plural = "GitHub Tokens"
        ordering = ["-created_on"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(user__isnull=True) & Q(group__isnull=True))
                    | (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="release_githubtoken_owner_exclusive",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        owner = self.owner_display() or _("Unscoped")
        package = self.package.name if self.package_id else _("Any package")
        return f"{package} ({owner})"

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @classmethod
    def store_token(
        cls,
        token: str,
        *,
        package: Package | None,
        user=None,
        expires_at=None,
    ) -> "GithubToken":
        expires_at = expires_at or _default_expiration()
        user = user if getattr(user, "is_authenticated", False) else None
        record, _ = cls.objects.update_or_create(
            package=package,
            user=user,
            group=None,
            defaults={"token": token, "expires_at": expires_at},
        )
        return record

    @classmethod
    def resolve_for_release(
        cls,
        *,
        package: Package | None,
        user=None,
    ) -> str | None:
        now = timezone.now()
        qs = cls.objects.filter(expires_at__gt=now)
        if package is not None:
            qs = qs.filter(Q(package=package) | Q(package__isnull=True))
        else:
            qs = qs.filter(package__isnull=True)

        user = user if getattr(user, "is_authenticated", False) else None
        group_ids: list[int] = []
        if user is not None:
            group_ids = list(
                SecurityGroup.objects.filter(
                    id__in=user.groups.values_list("id", flat=True)
                ).values_list("id", flat=True)
            )

        owner_filter = Q(user__isnull=True, group__isnull=True)
        if user is not None:
            owner_filter |= Q(user=user)
            if group_ids:
                owner_filter |= Q(group_id__in=group_ids)
        qs = qs.filter(owner_filter)

        if user is not None:
            owner_priority = Case(
                When(user=user, then=Value(0)),
                When(group_id__in=group_ids, then=Value(1)),
                When(user__isnull=True, group__isnull=True, then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        else:
            owner_priority = Case(
                When(user__isnull=True, group__isnull=True, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )

        if package is not None:
            package_priority = Case(
                When(package=package, then=Value(0)),
                When(package__isnull=True, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        else:
            package_priority = Case(
                When(package__isnull=True, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )

        token = (
            qs.annotate(
                package_priority=package_priority,
                owner_priority=owner_priority,
            )
            .order_by("package_priority", "owner_priority", "-expires_at", "-pk")
            .values_list("token", flat=True)
            .first()
        )
        if not token:
            return None
        token = token.strip()
        return token or None
