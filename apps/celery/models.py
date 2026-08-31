"""Proxy models for Celery admin integration."""
from django_celery_beat.models import (
    ClockedSchedule,
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
    PeriodicTasks,
    SolarSchedule,
)


class PeriodicTaskProxy(PeriodicTask):
    class Meta:
        proxy = True
        app_label = "celery"
        verbose_name = PeriodicTask._meta.verbose_name
        verbose_name_plural = PeriodicTask._meta.verbose_name_plural


class PeriodicTasksProxy(PeriodicTasks):
    class Meta:
        proxy = True
        app_label = "celery"
        verbose_name = PeriodicTasks._meta.verbose_name
        verbose_name_plural = PeriodicTasks._meta.verbose_name_plural


class IntervalScheduleProxy(IntervalSchedule):
    class Meta:
        proxy = True
        app_label = "celery"
        verbose_name = IntervalSchedule._meta.verbose_name
        verbose_name_plural = IntervalSchedule._meta.verbose_name_plural


class CrontabScheduleProxy(CrontabSchedule):
    class Meta:
        proxy = True
        app_label = "celery"
        verbose_name = CrontabSchedule._meta.verbose_name
        verbose_name_plural = CrontabSchedule._meta.verbose_name_plural


class SolarScheduleProxy(SolarSchedule):
    class Meta:
        proxy = True
        app_label = "celery"
        verbose_name = SolarSchedule._meta.verbose_name
        verbose_name_plural = SolarSchedule._meta.verbose_name_plural


class ClockedScheduleProxy(ClockedSchedule):
    class Meta:
        proxy = True
        app_label = "celery"
        verbose_name = ClockedSchedule._meta.verbose_name
        verbose_name_plural = ClockedSchedule._meta.verbose_name_plural
