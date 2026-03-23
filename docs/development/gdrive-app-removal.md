# GDrive app removal and legacy upgrade path

The runtime `apps.gdrive` package has been retired. Google OAuth credentials now live under `apps.calendars.GoogleAccount`, which keeps calendar publishing integrated inside the suite instead of maintaining a disconnected Google Sheets side system.

## What changed

- `GoogleAccount` now belongs to the calendars app and remains the only live Google integration model.
- Legacy `GoogleSheet` and `GoogleSheetColumn` rows are archived into `calendars_archive_googlesheet` and `calendars_archive_googlesheetcolumn` during the retirement migration so historical sheet metadata remains available for operator export or audit.
- Historical `gdrive` migration imports remain available through `apps._legacy.gdrive_migration_only`, so upgrades can still satisfy prior migration dependencies without loading `apps.gdrive` as a live app.

## Operator note

Apply the calendars retirement migration before removing old application code from a deployment. That migration copies live `gdrive_googleaccount` rows into `calendars_googleaccount` and archives any legacy sheet metadata into explicit historical tables.

If you still need the archived sheet metadata outside Django, export `calendars_archive_googlesheet` and `calendars_archive_googlesheetcolumn` after the upgrade. Those archive tables preserve the final retired rows for audit or one-off extraction workflows.
