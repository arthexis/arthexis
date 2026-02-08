from __future__ import annotations

from django.apps import apps as django_apps
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .user_story import UserStory


@receiver(post_save, sender=UserStory)
def _queue_low_rating_user_story_issue(
    sender, instance: UserStory, created: bool, raw: bool, **kwargs
) -> None:
    instance.handle_post_save(created=created, raw=raw)


@receiver(post_delete, sender=UserStory)
def _invalidate_user_story_dashboard_rule_on_delete(sender, **_kwargs) -> None:
    DashboardRule = django_apps.get_model("counters", "DashboardRule")
    DashboardRule.invalidate_model_cache(sender)


@receiver(post_save, sender=UserStory)
def _invalidate_user_story_dashboard_rule_on_save(
    sender,
    created: bool,
    update_fields: frozenset[str] | None,
    **_kwargs,
) -> None:
    # Invalidate if a new story is created, if all fields are saved (update_fields is None),
    # or if one of the relevant fields for the rule has been updated.
    relevant_fields = {"status", "assign_to", "owner", "is_deleted"}
    if created or not update_fields or relevant_fields.intersection(update_fields):
        DashboardRule = django_apps.get_model("counters", "DashboardRule")
        DashboardRule.invalidate_model_cache(sender)
