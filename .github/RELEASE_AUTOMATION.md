# Release automation authorization

The Watchtower/release flow separates repository-local authority from cross-repository authority.

## Repository-local authority

Normal `GITHUB_TOKEN` permissions are used for operations that stay inside the repository running the workflow, including:

- creating or verifying that repository's release tag;
- creating or reconciling that repository's GitHub Release;
- reading pull-request metadata needed to determine release intent.

Watchtower acceptance does not create version-advancement branches or pull requests. Version advancement is requested explicitly when needed.

## Cross-repository authority

`RELEASE_AUTOMATION_TOKEN` is reserved for cross-repository coordination:

1. Gway -> Arthexis candidate notification:
   - create a `repository_dispatch` event in `arthexis/arthexis`.
2. Accepted Watchtower pair -> package publisher dispatch:
   - dispatch the certified release workflow in the relevant repository.
3. Publisher -> central release evidence:
   - create immutable files under
     `.watchtower/releases/<package>/<version>.json`
     on the `watchtower-state` branch in `arthexis/arthexis`.

The token is not used to create or update source-repository pull requests.

## Minimum repository access

The credential should be installed or scoped only to:

- `arthexis/arthexis`
- `arthexis/gway`

It requires only the repository permissions needed by the operations above:

- **Actions: write** on both repositories, for workflow dispatch.
- **Contents: write** on `arthexis/arthexis`, for repository dispatch and release-evidence writes.

It does not need pull-request write permission, release/tag write permission in Gway, issue permission, administration permission, secrets permission, or broader organization access.

GitHub does not expose the permission scope of an Actions secret to the workflow itself. Repository code can constrain where the credential is used, but confirming that the stored token or GitHub App installation has no broader permissions is an external GitHub configuration check.

## Deployment and release handoff

A normal merged change may trigger Watchtower convergence. Once Watchtower records an accepted Arthexis/Gway pair, that acceptance is terminal with respect to source-repository mutation.

The accepted pair may then dispatch certified package publication when release intent exists. Publication reconciles artifacts and durable release evidence for the accepted SHAs; it does not advance either repository's source version.

Manual version advancement remains a separate, explicit repository change and can continue to use the `version-only` CI path when appropriate.
