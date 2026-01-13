# Package Release Workflow

The publish workflow coordinates multiple guarded steps to move a package version from source control to public repositories. The flow below mirrors the `PUBLISH_STEPS` sequence used by the release UI and the headless scheduler.

```mermaid
flowchart TD
    A[Start release from admin or scheduler] --> B[Check version number availability]
    B -->|Clean repo & ensure version not on PyPI| C[Freeze, squash and approve migrations]
    C --> D[Execute pre-release actions]
    D -->|Sync main, bump VERSION, stage fixtures| E[Build release artifacts]
    E -->|Promote build, commit metadata, push| F[Complete test suite with --all flag]
    F --> G[Get Release Manager Approval]
    G -->|Credentials provided & approval logged| H[Upload final build to PyPI]
    H --> I[Record publish URLs & update fixtures]
    I --> J[Finish and log completion]
    G -->|Missing credentials| G
    B -->|Dirty repo or version conflict| B
```

## Step-by-step breakdown

1. **Check version number availability** – Syncs with `origin/main`, enforces a clean working tree (auto-committing allowed fixture/`VERSION` changes when possible), and confirms the requested version is newer than the repository state and not already present on PyPI. The step halts on dirty repositories or conflicting versions to avoid accidental overwrites.
2. **Freeze, squash and approve migrations** – Serves as a manual checkpoint to review migration changes. The workflow logs the acknowledgement and expects developers to ensure migrations are consolidated appropriately before proceeding.
3. **Execute pre-release actions** – Refreshes release fixtures, updates the `VERSION` file to the target value, stages the changes, and commits them if anything changed. The workflow also tracks the pre-sync version to support clean restarts.
4. **Build release artifacts** – Re-validates that `origin/main` is unchanged, promotes the build via `release_utils.promote`, and commits any updated metadata (e.g., `VERSION`, release fixtures). The step sets the build revision and renames the log to the release-specific filename, ensuring traceability.
5. **Complete test suite with --all flag** – Captures the expectation that the full test suite has been executed with the `--all` flag. The UI records acknowledgement, keeping the workflow consistent even when tests run externally.
6. **Get Release Manager Approval** – Requires PyPI publishing credentials. For scheduled runs, auto-approval is logged when credentials exist; otherwise the workflow pauses until a release manager approves or rejects the release. Missing credentials are surfaced explicitly in the log.
7. **Upload final build to PyPI** – Publishes to the configured targets (including PyPI and optional GitHub registries), records any warnings, stores the resulting package URLs, and persists fixture updates. The workflow also commits and pushes publish metadata when fixtures change.

## Operational notes

- The same step sequence is executed by the headless scheduler through `run_headless_publish`, which builds a `NodeWorkflow` from `PUBLISH_STEPS` and writes progress logs under `LOG_DIR`.
- Dry-run mode exercises build and publish commands against Test PyPI, restoring `VERSION` and `pyproject.toml` afterward to avoid polluting the working tree.
- Repository hygiene safeguards (dirty checks, syncs against `origin/main`, and build stale detection) ensure releases restart when source changes appear mid-run.

## Proposal: adopt PyPI Trusted Publishers (OIDC)

To remove long-lived PyPI API tokens from the release workflow, we can delegate publishing to GitHub Actions using PyPI Trusted Publishers (OIDC). This preserves the current release manager approvals while shifting the final publish step to an audited, short-lived credential flow.

### Suggested workflow changes

1. **Register a trusted publisher in PyPI** for the `arthexis` project that targets the GitHub Actions workflow used for releases. Capture the required configuration fields (GitHub owner, repository name, workflow filename such as `publish.yml`, and the optional GitHub environment name if using environment protection rules). This ties the project to the repository, workflow file path, and branch or tag protection rules.
2. **Split release into two phases**:
   - Keep the current release UI/headless workflow through step 6 for approvals, metadata, and artifact generation.
   - Export the built artifacts (wheel/sdist) as a workflow artifact and trigger a GitHub Actions `publish` workflow that performs the upload with OIDC (for example, using a `workflow_run` trigger from the artifact-generation workflow to avoid a PAT-backed `workflow_dispatch` call).
3. **Create a release publish workflow** (example: `.github/workflows/publish.yml`) that:
   - Has `permissions: id-token: write` and `contents: read`.
   - Downloads the build artifacts from the release process.
   - Uses a pinned `pypa/gh-action-pypi-publish` version (for example, `pypa/gh-action-pypi-publish@v1.8.11`) with `skip-existing: false` and no API token configured, relying on OIDC instead.
4. **Gate publishing with release manager approval** by:
   - Requiring the workflow to be manually dispatched (or triggered by a protected tag) after approval.
   - Using GitHub environment protection rules (required reviewers) to enforce human approval before the publish job runs.
5. **Capture published URLs and logs** by pulling the resulting upload metadata back into the release fixtures (mirroring today’s step 7), ensuring traceability remains intact.

### Implementation notes

- The new `publish.yml` workflow is designed to build from a release tag and publish to PyPI or Test PyPI using OIDC.
- Configure the PyPI trusted publisher to match the repository, workflow path, and tag patterns (for example, `v*`).
- Use the `pypi` environment in GitHub with required reviewers to preserve release manager sign-off before the job publishes.

### Advantages

- **Credential-free publishing**: no API tokens stored in secrets, reducing rotation and leak risk.
- **Auditable, short-lived credentials**: PyPI only trusts GitHub’s OIDC token for the workflow + repository.
- **Clear separation of duties**: release management remains in the UI; publishing is automated and policy-controlled.

### Next steps

- Identify the release workflow entry point (tag or manual dispatch) and align it with the PyPI trusted publisher settings.
- Prototype the GitHub Actions publish workflow against Test PyPI, then switch the trusted publisher to production once validated.
