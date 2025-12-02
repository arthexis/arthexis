# Retroactive migration branch guards

Retroactive edits to Django migrations are occasionally unavoidable, but they
leave existing databases vulnerable to silent drift. To make the impact
explicit we now provide two custom migration operations:

- `BranchSplinterOperation` records that a migration line was retroactively
  rewritten.
- `BranchMergeOperation` confirms that the splinter marker was encountered
  earlier in the migration history.

Both operations live in `utils.migration_branches` and can be injected into
existing migrations that needed retroactive changes.

## How to use

1. Choose a stable `branch_id` that identifies the retroactive change. A
   reverse-DNS string plus a date (for example, `com.arthexis.retrofit-2025-02`)
   works well.
2. Insert `BranchSplinterOperation(branch_id, migration_label="app.0005")`
   into the first migration that was edited retroactively.
3. Insert `BranchMergeOperation(branch_id, migration_label="app.0010")` into
   the last migration touched by the retroactive edit.

New deployments will hit the splinter before the merge and proceed normally.
If an environment already migrated past the edited line using the old code, it
will encounter the merge without seeing the splinter and a
`MissingBranchSplinterError` will be raised. The error message explains that
the database must be rebuilt or rolled back to the splinter point before
continuing.

## Refresh script integration

`env-refresh.py` surfaces `MissingBranchSplinterError` separately from other
migration failures so engineers immediately know they are dealing with a
retroactively edited branch. The script will print the branch identifier and
merge migration involved, then stop so the operator can recreate the database
or roll it back before re-running the installer.
