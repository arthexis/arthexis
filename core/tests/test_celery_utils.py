import os

import django
from django.test import TestCase

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core.celery_utils import (
    normalize_periodic_task_name,
    periodic_task_name_variants,
    slugify_task_name,
)


class CeleryUtilsTests(TestCase):
    def test_slugify_task_name_replaces_dots_and_underscores(self):
        assert slugify_task_name("nodes.tasks.capture_node_screenshot") == (
            "nodes-tasks-capture-node-screenshot"
        )

    def test_periodic_task_name_variants_include_legacy(self):
        variants = periodic_task_name_variants("pages_purge_landing_leads")
        assert variants == {
            "pages_purge_landing_leads",
            "pages-purge-landing-leads",
        }

    def test_normalize_periodic_task_name_updates_existing_row(self):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=5, period=IntervalSchedule.MINUTES
        )
        task = PeriodicTask.objects.create(
            name="pages_purge_landing_leads",
            interval=schedule,
            task="pages.tasks.purge_expired_landing_leads",
        )

        normalized = normalize_periodic_task_name(
            PeriodicTask.objects, "pages_purge_landing_leads"
        )

        task.refresh_from_db()
        assert normalized == "pages-purge-landing-leads"
        assert task.name == normalized

    def test_normalize_periodic_task_name_removes_legacy_duplicates(self):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=10, period=IntervalSchedule.MINUTES
        )
        slug_task = PeriodicTask.objects.create(
            name="pages-purge-landing-leads",
            interval=schedule,
            task="pages.tasks.purge_expired_landing_leads",
        )
        legacy_task = PeriodicTask(
            name="pages_purge_landing_leads",
            interval=schedule,
            task="pages.tasks.purge_expired_landing_leads",
        )
        PeriodicTask._core_fixture_original_save(legacy_task)

        normalized = normalize_periodic_task_name(
            PeriodicTask.objects, "pages_purge_landing_leads"
        )

        assert normalized == "pages-purge-landing-leads"
        assert PeriodicTask.objects.filter(pk=slug_task.pk).exists()
        assert not PeriodicTask.objects.filter(pk=legacy_task.pk).exists()
        assert not PeriodicTask.objects.filter(
            name="pages_purge_landing_leads"
        ).exists()

    def test_periodic_task_save_updates_existing_slug_from_legacy_name(self):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        fast_schedule, _ = IntervalSchedule.objects.get_or_create(
            every=1, period=IntervalSchedule.MINUTES
        )
        slow_schedule, _ = IntervalSchedule.objects.get_or_create(
            every=30, period=IntervalSchedule.MINUTES
        )
        task = PeriodicTask.objects.create(
            name="pages-purge-landing-leads",
            interval=fast_schedule,
            task="pages.tasks.purge_expired_landing_leads",
        )

        legacy_update = PeriodicTask(
            name="pages_purge_landing_leads",
            interval=slow_schedule,
            task=task.task,
        )
        legacy_update.save()

        legacy_update_pk = legacy_update.pk

        task.refresh_from_db()

        assert PeriodicTask.objects.filter(name="pages-purge-landing-leads").count() == 1
        assert not PeriodicTask.objects.filter(name="pages_purge_landing_leads").exists()
        assert legacy_update_pk == task.pk
        assert task.interval_id == slow_schedule.id
