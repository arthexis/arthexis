from __future__ import annotations

from django.db import migrations


ARCHIVE_TABLES = {
    "sheet": "calendars_archive_googlesheet",
    "sheet_column": "calendars_archive_googlesheetcolumn",
}


def migrate_google_accounts_from_gdrive(apps, schema_editor):
    """Copy live legacy Google account rows into the calendars-owned table."""
    connection = schema_editor.connection
    quote_name = connection.ops.quote_name
    table_names = set(connection.introspection.table_names())
    source_table = "gdrive_googleaccount"
    target_table = "calendars_googleaccount"

    if source_table not in table_names or target_table not in table_names:
        return

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {quote_name(target_table)} (
                id,
                is_seed_data,
                is_user_data,
                is_deleted,
                email,
                client_id,
                client_secret,
                refresh_token,
                access_token,
                token_expires_at,
                scopes,
                is_enabled,
                avatar_id,
                group_id,
                user_id
            )
            SELECT
                legacy.id,
                legacy.is_seed_data,
                legacy.is_user_data,
                legacy.is_deleted,
                legacy.email,
                legacy.client_id,
                legacy.client_secret,
                legacy.refresh_token,
                legacy.access_token,
                legacy.token_expires_at,
                legacy.scopes,
                legacy.is_enabled,
                legacy.avatar_id,
                legacy.group_id,
                legacy.user_id
            FROM {quote_name(source_table)} AS legacy
            WHERE NOT EXISTS (
                SELECT 1
                FROM {quote_name(target_table)} AS current
                WHERE current.id = legacy.id
            )
            ORDER BY legacy.id
            """
        )


def restore_google_accounts_to_gdrive(apps, schema_editor):
    """Restore Google account rows back into the legacy gdrive table on reversal."""
    connection = schema_editor.connection
    quote_name = connection.ops.quote_name
    table_names = set(connection.introspection.table_names())
    source_table = "calendars_googleaccount"
    target_table = "gdrive_googleaccount"

    if source_table not in table_names or target_table not in table_names:
        return

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {quote_name(target_table)} (
                id,
                is_seed_data,
                is_user_data,
                is_deleted,
                email,
                client_id,
                client_secret,
                refresh_token,
                access_token,
                token_expires_at,
                scopes,
                is_enabled,
                avatar_id,
                group_id,
                user_id
            )
            SELECT
                current.id,
                current.is_seed_data,
                current.is_user_data,
                current.is_deleted,
                current.email,
                current.client_id,
                current.client_secret,
                current.refresh_token,
                current.access_token,
                current.token_expires_at,
                current.scopes,
                current.is_enabled,
                current.avatar_id,
                current.group_id,
                current.user_id
            FROM {quote_name(source_table)} AS current
            WHERE NOT EXISTS (
                SELECT 1
                FROM {quote_name(target_table)} AS legacy
                WHERE legacy.id = current.id
            )
            ORDER BY current.id
            """
        )


def archive_google_sheet_rows(apps, schema_editor):
    """Archive legacy sheet metadata into explicit historical tables."""
    connection = schema_editor.connection
    quote_name = connection.ops.quote_name
    table_names = set(connection.introspection.table_names())
    sheet_table = "gdrive_googlesheet"
    column_table = "gdrive_googlesheetcolumn"

    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {quote_name(ARCHIVE_TABLES['sheet_column'])}")
        if column_table in table_names:
            cursor.execute(
                f"""
                INSERT INTO {quote_name(ARCHIVE_TABLES['sheet_column'])} (
                    legacy_id,
                    is_seed_data,
                    is_user_data,
                    is_deleted,
                    worksheet,
                    name,
                    position,
                    detected_type,
                    sheet_id
                )
                SELECT
                    id,
                    is_seed_data,
                    is_user_data,
                    is_deleted,
                    worksheet,
                    name,
                    position,
                    detected_type,
                    sheet_id
                FROM {quote_name(column_table)}
                ORDER BY id
                """
            )

        cursor.execute(f"DELETE FROM {quote_name(ARCHIVE_TABLES['sheet'])}")
        if sheet_table in table_names:
            cursor.execute(
                f"""
                INSERT INTO {quote_name(ARCHIVE_TABLES['sheet'])} (
                    legacy_id,
                    is_seed_data,
                    is_user_data,
                    is_deleted,
                    name,
                    spreadsheet_id,
                    default_worksheet,
                    metadata_json,
                    is_enabled,
                    account_id
                )
                SELECT
                    id,
                    is_seed_data,
                    is_user_data,
                    is_deleted,
                    name,
                    spreadsheet_id,
                    default_worksheet,
                    metadata,
                    is_enabled,
                    account_id
                FROM {quote_name(sheet_table)}
                ORDER BY id
                """
            )


def restore_google_sheet_rows(apps, schema_editor):
    """Restore archived sheet metadata into the legacy tables on reversal."""
    connection = schema_editor.connection
    quote_name = connection.ops.quote_name
    table_names = set(connection.introspection.table_names())
    sheet_table = "gdrive_googlesheet"
    column_table = "gdrive_googlesheetcolumn"

    with connection.cursor() as cursor:
        if sheet_table in table_names:
            cursor.execute(
                f"""
                INSERT INTO {quote_name(sheet_table)} (
                    id,
                    is_seed_data,
                    is_user_data,
                    is_deleted,
                    name,
                    spreadsheet_id,
                    default_worksheet,
                    metadata,
                    is_enabled,
                    account_id
                )
                SELECT
                    archive.legacy_id,
                    archive.is_seed_data,
                    archive.is_user_data,
                    archive.is_deleted,
                    archive.name,
                    archive.spreadsheet_id,
                    archive.default_worksheet,
                    archive.metadata_json,
                    archive.is_enabled,
                    archive.account_id
                FROM {quote_name(ARCHIVE_TABLES['sheet'])} AS archive
                WHERE NOT EXISTS (
                    SELECT 1 FROM {quote_name(sheet_table)} AS current WHERE current.id = archive.legacy_id
                )
                ORDER BY archive.legacy_id
                """
            )

        if column_table in table_names:
            cursor.execute(
                f"""
                INSERT INTO {quote_name(column_table)} (
                    id,
                    is_seed_data,
                    is_user_data,
                    is_deleted,
                    worksheet,
                    name,
                    position,
                    detected_type,
                    sheet_id
                )
                SELECT
                    archive.legacy_id,
                    archive.is_seed_data,
                    archive.is_user_data,
                    archive.is_deleted,
                    archive.worksheet,
                    archive.name,
                    archive.position,
                    archive.detected_type,
                    archive.sheet_id
                FROM {quote_name(ARCHIVE_TABLES['sheet_column'])} AS archive
                WHERE NOT EXISTS (
                    SELECT 1 FROM {quote_name(column_table)} AS current WHERE current.id = archive.legacy_id
                )
                ORDER BY archive.legacy_id
                """
            )


class Migration(migrations.Migration):

    dependencies = [
        ("calendars", "0002_rework_calendars_for_outbound_push"),
        ("gdrive", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                f"""
                CREATE TABLE IF NOT EXISTS {ARCHIVE_TABLES['sheet']} (
                    legacy_id bigint PRIMARY KEY,
                    is_seed_data boolean NOT NULL,
                    is_user_data boolean NOT NULL,
                    is_deleted boolean NOT NULL,
                    name varchar(255) NOT NULL,
                    spreadsheet_id varchar(255) NOT NULL,
                    default_worksheet varchar(255) NOT NULL,
                    metadata_json text NOT NULL,
                    is_enabled boolean NOT NULL,
                    account_id bigint NULL
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS {ARCHIVE_TABLES['sheet_column']} (
                    legacy_id bigint PRIMARY KEY,
                    is_seed_data boolean NOT NULL,
                    is_user_data boolean NOT NULL,
                    is_deleted boolean NOT NULL,
                    worksheet varchar(255) NOT NULL,
                    name varchar(255) NOT NULL,
                    position integer NOT NULL,
                    detected_type varchar(20) NOT NULL,
                    sheet_id bigint NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS calendars_googleaccount (
                    id bigint NOT NULL PRIMARY KEY,
                    is_seed_data boolean NOT NULL,
                    is_user_data boolean NOT NULL,
                    is_deleted boolean NOT NULL,
                    email varchar(254) NOT NULL,
                    client_id varchar(255) NOT NULL,
                    client_secret varchar(255) NOT NULL,
                    refresh_token varchar(255) NOT NULL,
                    access_token varchar(255) NOT NULL,
                    token_expires_at datetime NULL,
                    scopes text NOT NULL,
                    is_enabled boolean NOT NULL,
                    avatar_id bigint NULL REFERENCES chats_chatavatar (id) DEFERRABLE INITIALLY DEFERRED,
                    group_id bigint NULL REFERENCES groups_securitygroup (id) DEFERRABLE INITIALLY DEFERRED,
                    user_id bigint NULL REFERENCES users_user (id) DEFERRABLE INITIALLY DEFERRED
                )
                """,
            ],
            reverse_sql=[
                f"DROP TABLE IF EXISTS {ARCHIVE_TABLES['sheet_column']}",
                f"DROP TABLE IF EXISTS {ARCHIVE_TABLES['sheet']}",
                migrations.RunSQL.noop,
            ],
        ),
        migrations.RunPython(migrate_google_accounts_from_gdrive, restore_google_accounts_to_gdrive),
        migrations.RunPython(archive_google_sheet_rows, restore_google_sheet_rows),
    ]
