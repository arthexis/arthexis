# Survey app removal and legacy data archival path

The runtime `survey` Django app has been removed from automatic discovery and its `/survey/...` routes no longer resolve.
A legacy migration-only app now carries the historical migration chain under `apps/_legacy/survey_migration_only/` so existing installations can archive historical survey rows before the live tables are dropped.

## Release note for operators

If you still rely on `SurveyResult.data`, export it before upgrading to a release that applies `survey.0002_archive_and_drop_survey_models`.
A straightforward export on a pre-upgrade release is:

```bash
./env-refresh.sh --deps-only
.venv/bin/python manage.py dumpdata survey.SurveyResult --indent 2 > survey-results.json
```

You may also export the full survey schema if you need topic and question context:

```bash
.venv/bin/python manage.py dumpdata survey.SurveyTopic survey.SurveyQuestion survey.SurveyResult --indent 2 > survey-export.json
```

After upgrade, the live `survey_*` tables are removed and their rows are preserved in archived tables created by the migration:

- `survey_archivedsurveytopic`
- `survey_archivedsurveyquestion`
- `survey_archivedsurveyresult`

Reversing the migration restores the original `survey_*` tables and copies archived rows back into them.
