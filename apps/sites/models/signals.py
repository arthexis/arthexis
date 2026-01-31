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


@receiver(post_save, sender=UserStory)
@receiver(post_delete, sender=UserStory)
def _invalidate_user_story_dashboard_rule(
    sender, instance: UserStory, **_kwargs
) -> None:
    DashboardRule = django_apps.get_model("counters", "DashboardRule")
    DashboardRule.invalidate_model_cache(sender)
