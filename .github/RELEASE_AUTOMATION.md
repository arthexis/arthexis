# Release automation authorization

The Watchtower/PyPI release flow separates repository-local authority from cross-repository authority.

## Repository-local authority

Normal `GITHUB_TOKEN` permissions are used for operations that stay inside the repository running the workflow:

- create or verify that repository's release tag;
- create or reconcile that repository's GitHub Release;
- open the repository-local next-version pull request.

The cross-repository credential must not be used for these operations.

## Cross-repository authority

`RELEASE_AUTOMATION_TOKEN` is reserved for these operations only:

1. Gway -> Arthexis candidate notification:
   - create a `repository_dispatch` event in `arthexis/arthexis`.
2. Arthexis Watchtower -> package publisher dispatch:
   - dispatch `.github/workflows/pypi-publish.yml` in `arthexis/arthexis`;
   - dispatch `.github/workflows/pypi-publish.yml` in `arthexis/gway`.
3. Publisher -> central release evidence:
   - create immutable files under
     `.watchtower/releases/<package>/<version>.json`
     on the `watchtower-state` branch in `arthexis/arthexis`.

## Minimum repository access

The credential should be installed or scoped only to:

- `arthexis/arthexis`
- `arthexis/gway`

It requires only the repository permissions needed by the operations above:

- **Actions: write** on both repositories, for workflow dispatch.
- **Contents: write** on `arthexis/arthexis`, for repository dispatch and release-evidence writes.

It does not need pull-request write permission, release/tag write permission in Gway, issue permission, administration permission, secrets permission, or broader organization access.

GitHub does not expose the permission scope of an Actions secret to the workflow itself. Therefore repository code can constrain where the credential is used, but confirming that the stored token/GitHub App installation has no broader permissions is an external GitHub configuration check.
