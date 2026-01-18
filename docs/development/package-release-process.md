# Package Release Workflow

The publish workflow coordinates multiple guarded steps to move a package version from source control to public repositories. The flow below mirrors the `PUBLISH_STEPS` sequence used by the release UI and the headless scheduler.

```mermaid
flowchart TD
    A[Start release from admin or scheduler] --> B[Check version number availability]
    B -->|Clean repo & ensure version not on PyPI| C[Freeze, squash and approve migrations]
    C --> D[Execute pre-release actions]
    D -->|Sync main, bump VERSION, stage fixtures| E[Build release artifacts]
    E -->|Promote build, commit metadata, push| F[Complete test suite with --all flag]
    F --> TP[Confirm PyPI Trusted Publisher settings]
    TP --> G[Verify release environment]
    G -->|Environment ready| H[Export artifacts and push release tag]
    H --> I[Wait for GitHub Actions publish]
    I --> J[Record publish URLs & update fixtures after publish]
    J --> K[Capture PyPI publish logs]
    K --> L[Finish and log completion]
    B -->|Dirty repo or version conflict| B
```

## Step-by-step breakdown

1. **Check version number availability** – Syncs with `origin/main`, enforces a clean working tree (auto-committing allowed fixture/`VERSION` changes when possible), and confirms the requested version is newer than the repository state and not already present on PyPI. The step halts on dirty repositories or conflicting versions to avoid accidental overwrites.
2. **Freeze, squash and approve migrations** – Serves as a manual checkpoint to review migration changes. The workflow logs the acknowledgement and expects developers to ensure migrations are consolidated appropriately before proceeding.
3. **Execute pre-release actions** – Refreshes release fixtures, updates the `VERSION` file to the target value, stages the changes, and commits them if anything changed. The workflow also tracks the pre-sync version to support clean restarts.
4. **Build release artifacts** – Re-validates that `origin/main` is unchanged, promotes the build via `release_utils.promote`, and commits any updated metadata (e.g., `VERSION`, release fixtures). The step sets the build revision and renames the log to the release-specific filename, ensuring traceability.
5. **Complete test suite with --all flag** – Captures the expectation that the full test suite has been executed with the `--all` flag. The UI records acknowledgement, keeping the workflow consistent even when tests run externally.
6. **Confirm PyPI Trusted Publisher settings** – Verify the PyPI project settings include the Trusted Publisher entry that matches the repository, workflow file, tag pattern, and GitHub environment used by the publish workflow.
7. **Verify release environment** – Ensure the release environment can push tags to `origin/main` and has a GitHub token (for GitHub API operations like creating releases and fetching workflow runs). Missing requirements are reported with instructions before the publish step continues. In GitHub Actions, map `secrets.GITHUB_TOKEN` into `GITHUB_TOKEN`/`GH_TOKEN` so the release tools can read it.
8. **Export artifacts and push release tag** – Uploads the built wheel/sdist artifacts to the GitHub release for the version tag and pushes the tag to GitHub. The `publish.yml` workflow listens for tag pushes and publishes to PyPI via OIDC.
9. **Wait for GitHub Actions publish** – The workflow pauses until the publish workflow completes, logging the GitHub Actions run URL when available so operators can monitor progress.
10. **Record publish URLs & update fixtures** – After the GitHub Actions publish completes (and the release is visible on PyPI), the workflow records the PyPI/GitHub URLs, updates fixtures, and commits the publish metadata.
11. **Capture PyPI publish logs** – Downloads the GitHub Actions publish run logs, stores the PyPI upload results, and persists them into the release fixtures for traceability.

## Operational notes

- The same step sequence is executed by the headless scheduler through `run_headless_publish`, which builds a `NodeWorkflow` from `PUBLISH_STEPS` and writes progress logs under `LOG_DIR`.
- Dry-run mode exercises build and publish commands against Test PyPI, restoring `VERSION` and `pyproject.toml` afterward to avoid polluting the working tree.
- Repository hygiene safeguards (dirty checks, syncs against `origin/main`, and build stale detection) ensure releases restart when source changes appear mid-run.

## Publish with PyPI Trusted Publishers (OIDC)

To remove long-lived PyPI API tokens from the release workflow, publishing is delegated to GitHub Actions using PyPI Trusted Publishers (OIDC). This shifts the final publish step to an audited, short-lived credential flow and removes the need to store release credentials in fixtures.

### Workflow changes

1. **Enable OIDC publishing on the package** – In the Release Package admin, enable the OIDC publish toggle so the suite knows to skip PyPI credentials and rely on GitHub Actions publishing.
2. **Register a trusted publisher in PyPI** for the `arthexis` project that targets the GitHub Actions workflow used for releases. Capture the required configuration fields (GitHub owner, repository name, workflow filename such as `publish.yml`, and the optional GitHub environment name if using environment protection rules). This ties the project to the repository, workflow file path, and branch or tag protection rules.
3. **Split release into two phases**:
   - The release UI/headless workflow runs through metadata prep and artifact generation.
   - The workflow exports built artifacts (wheel/sdist) to the GitHub release and pushes the release tag, which triggers the GitHub Actions `publish` workflow for OIDC uploads.
4. **Release publish workflow** (example: `.github/workflows/publish.yml`) that:
   - Builds the sdist and wheel in a dedicated job, uploads them as artifacts, and publishes in a separate job.
   - Uses `permissions: id-token: write` on the publish job and `permissions: contents: write` at the workflow level to allow tags/releases.
   - Uses `pypa/gh-action-pypi-publish@release/v1` with `attestations: true` and no API token configured, relying on OIDC instead.
   - Exposes `secrets.GITHUB_TOKEN` as `GITHUB_TOKEN`/`GH_TOKEN` so release automation can create GitHub releases, upload assets, and check workflow runs.
5. **Gate publishing with environment protection rules** by:
   - Triggering the workflow from a protected tag.
   - Using GitHub environment protection rules (required reviewers) to enforce human approval before the publish job runs when policy requires it.
6. **Capture published URLs and logs** by pulling the resulting upload metadata back into the release fixtures (mirroring today’s step 8), ensuring traceability remains intact.

### Implementation notes

- The new `publish.yml` workflow is designed to build from a release tag and publish to PyPI using OIDC.
- Configure the PyPI trusted publisher to match the repository, workflow path, and tag patterns (for example, `v*`).
- Use the `pypi` environment in GitHub with required reviewers if a human approval gate is required before the job publishes.

### PyPI Trusted Publisher configuration (required)

Add (or update) the Trusted Publisher entry in the PyPI project settings for `arthexis` with the exact values below so the OIDC publish workflow can authenticate:

| Setting                    | Value                           | Notes                                                                                          |
| -------------------------- | ------------------------------- | ---------------------------------------------------------------------------------------------- |
| Owner                      | `arthexis`                      |                                                                                                |
| Repository                 | `arthexis`                      |                                                                                                |
| Workflow file              | `.github/workflows/publish.yml` |                                                                                                |
| Workflow ref / tag pattern | `refs/tags/v*`                  | Run the workflow dispatch from the tag so the OIDC `ref` claim matches this pattern.           |
| GitHub environment         | `pypi`                          | Must match `environment: pypi` in `.github/workflows/publish.yml`.                              |

If the workflow is triggered from a branch, the OIDC `ref` claim will be `refs/heads/<branch>`. In that case, add an additional trusted publisher entry that matches the branch ref (for example, `refs/heads/main`) or ensure the workflow only runs from release tags.

### Advantages

- **Credential-free publishing**: no API tokens stored in secrets, reducing rotation and leak risk.
- **Auditable, short-lived credentials**: PyPI only trusts GitHub’s OIDC token for the workflow + repository.
- **Clear separation of duties**: release management remains in the UI; publishing is automated and policy-controlled.

## GitHub Actions release environment requirements

The release verification step checks that a GitHub token is available in the environment. GitHub Actions provides a `secrets.GITHUB_TOKEN`, but it is **not** automatically exported as `GITHUB_TOKEN`. Add an `env` block for the job (or workflow) that runs the publish process:

```yaml
permissions:
  contents: write
  id-token: write

jobs:
  publish-to-pypi:
    env:
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

If you need to restrict access to `main`, enforce branch protection rules on GitHub; the token will still be able to create releases/tags while respecting those protections.

## Git authentication for tag pushes

When the release workflow needs to push a tag to GitHub (for example during the
"Export artifacts and push release tag" step), the release runner
must be authenticated to `origin`. If the logs show authentication failures
like `fatal: could not read Username for 'https://github.com'`, a release
operator needs to authenticate before retrying the publish step or pushing the
tag manually.

Use one of the following options:

- **Local authentication**: Run `git push origin vX.Y.Z` from an authenticated
  environment (for example a shell with a GitHub token or SSH key configured).
- **Environment token**: Ensure `GITHUB_TOKEN` or `GH_TOKEN` is available in the
  release environment and that your git credential helper can use it for HTTPS
  pushes.

### Next steps

- Identify the release workflow entry point (tag) and align it with the PyPI trusted publisher settings.
- Prototype the GitHub Actions publish workflow against Test PyPI, then switch the trusted publisher to production once validated.
