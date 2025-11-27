# CI Troubleshooting

## `fatal: origin/main...HEAD: no merge base`

This Git error appears when the workflow tries to compute the merge base between the branch under test (`HEAD`) and the requested base ref (for example `origin/main`), but the local checkout does not contain a common ancestor commit. In GitHub Actions this typically happens when both refs are fetched with a very shallow history (for example `--depth=1`), so the shared merge base commit is not available locally.

To resolve it:
- Fetch the base ref with a larger depth or unshallow the repository (e.g. `git fetch origin "$BASE_REF" --deepen=50` or `git fetch --unshallow`) before running commands that need a merge base.
- Ensure `BASE_REF` points to the correct upstream branch and that your feature branch is descended from it.

Increasing the fetch depth restores the missing ancestor commit so `git merge-base origin/main HEAD` can succeed.
