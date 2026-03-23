from __future__ import annotations

import json

from django.db import migrations, models


ARCHIVE_TABLES = {
    "dispatch": "calendars_archive_calendareventdispatch",
    "snapshot": "calendars_archive_calendareventsnapshot",
    "trigger": "calendars_archive_calendareventtrigger",
}


def archive_calendar_event_rows(apps, schema_editor):
    """Copy legacy calendar trigger data into archive tables before dropping models."""
    CalendarEventDispatch = apps.get_model("calendars", "CalendarEventDispatch")
    CalendarEventSnapshot = apps.get_model("calendars", "CalendarEventSnapshot")
    CalendarEventTrigger = apps.get_model("calendars", "CalendarEventTrigger")

    connection = schema_editor.connection
    quote_name = connection.ops.quote_name

    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {quote_name(ARCHIVE_TABLES['dispatch'])}")
        for dispatch in CalendarEventDispatch.objects.order_by("pk").values():
            cursor.execute(
                f"""
                INSERT INTO {quote_name(ARCHIVE_TABLES['dispatch'])} (
                    legacy_id,
                    is_seed_data,
                    is_user_data,
                    is_deleted,
                    event_id,
                    event_updated,
                    trigger_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    dispatch["id"],
                    dispatch["is_seed_data"],
                    dispatch["is_user_data"],
                    dispatch["is_deleted"],
                    dispatch["event_id"],
                    dispatch["event_updated"],
                    dispatch["trigger_id"],
                ],
            )

        cursor.execute(f"DELETE FROM {quote_name(ARCHIVE_TABLES['snapshot'])}")
        for snapshot in CalendarEventSnapshot.objects.order_by("pk").values():
            cursor.execute(
                f"""
                INSERT INTO {quote_name(ARCHIVE_TABLES['snapshot'])} (
                    legacy_id,
                    is_seed_data,
                    is_user_data,
                    is_deleted,
                    event_id,
                    summary,
                    location,
                    starts_at,
                    ends_at,
                    event_updated,
                    raw_json,
                    calendar_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    snapshot["id"],
                    snapshot["is_seed_data"],
                    snapshot["is_user_data"],
                    snapshot["is_deleted"],
                    snapshot["event_id"],
                    snapshot["summary"],
                    snapshot["location"],
                    snapshot["starts_at"],
                    snapshot["ends_at"],
                    snapshot["event_updated"],
                    json.dumps(snapshot["raw"]),
                    snapshot["calendar_id"],
                ],
            )

        cursor.execute(f"DELETE FROM {quote_name(ARCHIVE_TABLES['trigger'])}")
        for trigger in CalendarEventTrigger.objects.order_by("pk").values():
            cursor.execute(
                f"""
                INSERT INTO {quote_name(ARCHIVE_TABLES['trigger'])} (
                    legacy_id,
                    is_seed_data,
                    is_user_data,
                    is_deleted,
                    name,
                    task_name,
                    lead_time_minutes,
                    summary_contains,
                    location_contains,
                    is_enabled,
                    calendar_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    trigger["id"],
                    trigger["is_seed_data"],
                    trigger["is_user_data"],
                    trigger["is_deleted"],
                    trigger["name"],
                    trigger["task_name"],
                    trigger["lead_time_minutes"],
                    trigger["summary_contains"],
                    trigger["location_contains"],
                    trigger["is_enabled"],
                    trigger["calendar_id"],
                ],
            )


def restore_calendar_event_rows(apps, schema_editor):
    """Restore archived calendar trigger data when reversing the migration."""
    CalendarEventDispatch = apps.get_model("calendars", "CalendarEventDispatch")
    CalendarEventSnapshot = apps.get_model("calendars", "CalendarEventSnapshot")
    CalendarEventTrigger = apps.get_model("calendars", "CalendarEventTrigger")

    connection = schema_editor.connection
    quote_name = connection.ops.quote_name

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                legacy_id,
                is_seed_data,
                is_user_data,
                is_deleted,
                name,
                task_name,
                lead_time_minutes,
                summary_contains,
                location_contains,
                is_enabled,
                calendar_id
            FROM {quote_name(ARCHIVE_TABLES['trigger'])}
            ORDER BY legacy_id
            """
        )
        trigger_rows = cursor.fetchall()
        CalendarEventTrigger.objects.bulk_create(
            [
                CalendarEventTrigger(
                    id=row[0],
                    is_seed_data=row[1],
                    is_user_data=row[2],
                    is_deleted=row[3],
                    name=row[4],
                    task_name=row[5],
                    lead_time_minutes=row[6],
                    summary_contains=row[7],
                    location_contains=row[8],
                    is_enabled=row[9],
                    calendar_id=row[10],
                )
                for row in trigger_rows
            ]
        )

        cursor.execute(
            f"""
            SELECT
                legacy_id,
                is_seed_data,
                is_user_data,
                is_deleted,
                event_id,
                summary,
                location,
                starts_at,
                ends_at,
                event_updated,
                raw_json,
                calendar_id
            FROM {quote_name(ARCHIVE_TABLES['snapshot'])}
            ORDER BY legacy_id
            """
        )
        snapshot_rows = cursor.fetchall()
        CalendarEventSnapshot.objects.bulk_create(
            [
                CalendarEventSnapshot(
                    id=row[0],
                    is_seed_data=row[1],
                    is_user_data=row[2],
                    is_deleted=row[3],
                    event_id=row[4],
                    summary=row[5],
                    location=row[6],
                    starts_at=row[7],
                    ends_at=row[8],
                    event_updated=row[9],
                    raw=json.loads(row[10] or "{}"),
                    calendar_id=row[11],
                )
                for row in snapshot_rows
            ]
        )

        cursor.execute(
            f"""
            SELECT
                legacy_id,
                is_seed_data,
                is_user_data,
                is_deleted,
                event_id,
                event_updated,
                trigger_id
            FROM {quote_name(ARCHIVE_TABLES['dispatch'])}
            ORDER BY legacy_id
            """
        )
        dispatch_rows = cursor.fetchall()
        CalendarEventDispatch.objects.bulk_create(
            [
                CalendarEventDispatch(
                    id=row[0],
                    is_seed_data=row[1],
                    is_user_data=row[2],
                    is_deleted=row[3],
                    event_id=row[4],
                    event_updated=row[5],
                    trigger_id=row[6],
                )
                for row in dispatch_rows
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("calendars", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                f"""
                CREATE TABLE IF NOT EXISTS {ARCHIVE_TABLES['trigger']} (
                    legacy_id bigint PRIMARY KEY,
                    is_seed_data boolean NOT NULL,
                    is_user_data boolean NOT NULL,
                    is_deleted boolean NOT NULL,
                    name varchar(255) NOT NULL,
                    task_name varchar(255) NOT NULL,
                    lead_time_minutes integer NOT NULL,
                    summary_contains varchar(255) NOT NULL,
                    location_contains varchar(255) NOT NULL,
                    is_enabled boolean NOT NULL,
                    calendar_id bigint NOT NULL
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS {ARCHIVE_TABLES['snapshot']} (
                    legacy_id bigint PRIMARY KEY,
                    is_seed_data boolean NOT NULL,
                    is_user_data boolean NOT NULL,
                    is_deleted boolean NOT NULL,
                    event_id varchar(255) NOT NULL,
                    summary varchar(500) NOT NULL,
                    location varchar(500) NOT NULL,
                    starts_at datetime NULL,
                    ends_at datetime NULL,
                    event_updated datetime NOT NULL,
                    raw_json text NOT NULL,
                    calendar_id bigint NOT NULL
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS {ARCHIVE_TABLES['dispatch']} (
                    legacy_id bigint PRIMARY KEY,
                    is_seed_data boolean NOT NULL,
                    is_user_data boolean NOT NULL,
                    is_deleted boolean NOT NULL,
                    event_id varchar(255) NOT NULL,
                    event_updated datetime NOT NULL,
                    trigger_id bigint NOT NULL
                )
                """,
            ],
            reverse_sql=[
                f"DROP TABLE IF EXISTS {ARCHIVE_TABLES['dispatch']}",
                f"DROP TABLE IF EXISTS {ARCHIVE_TABLES['snapshot']}",
                f"DROP TABLE IF EXISTS {ARCHIVE_TABLES['trigger']}",
            ],
        ),
        migrations.RunPython(archive_calendar_event_rows, restore_calendar_event_rows),
        migrations.DeleteModel(
            name="CalendarEventDispatch",
        ),
        migrations.DeleteModel(
            name="CalendarEventSnapshot",
        ),
        migrations.DeleteModel(
            name="CalendarEventTrigger",
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="account",
            field=models.ForeignKey(
                blank=True,
                help_text="Google account used to publish events to this calendar.",
                null=True,
                on_delete=models.SET_NULL,
                related_name="calendars",
                to="calendars.googleaccount",
            ),
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="calendar_id",
            field=models.CharField(
                help_text="Google Calendar ID that should receive outbound events.",
                max_length=255,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="is_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Disable to prevent new outbound event pushes to this calendar.",
            ),
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="metadata",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Optional deployment-owned metadata for outbound publishing.",
            ),
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="name",
            field=models.CharField(
                help_text="Friendly display name for this outbound calendar destination.",
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="timezone",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Default IANA timezone used when publishing events.",
                max_length=64,
            ),
        ),
        migrations.AddConstraint(
            model_name="googlecalendar",
            constraint=models.CheckConstraint(
                condition=models.Q(is_enabled=False) | models.Q(account__isnull=False),
                name="calendar_enabled_requires_account",
            ),
        ),
    ]
