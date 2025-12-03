from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from .module import Module
from .user_story import UserStory


@receiver(post_save, sender=UserStory)
def _queue_low_rating_user_story_issue(
    sender, instance: UserStory, created: bool, raw: bool, **kwargs
) -> None:
    instance.handle_post_save(created=created, raw=raw)


@receiver(post_save, sender=Module)
def _create_landings(sender, instance: Module, created: bool, raw: bool, **kwargs) -> None:
    instance.handle_post_save(created=created, raw=raw)
