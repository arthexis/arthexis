# Secret Scanning

The CI pipeline runs automated secret scans to prevent accidental exposure of credentials. The
`Secret Scan` workflow is triggered on pull requests for these activity types:

- `opened`
- `synchronize`
- `reopened`
- `ready_for_review`

It can also be run manually with `workflow_dispatch`.

Every run uses [Gitleaks](https://github.com/gitleaks/gitleaks) with the repository
[`.gitleaks.toml`](https://github.com/arthexis/arthexis/blob/main/.gitleaks.toml) configuration and executes two checks:

1. A working-tree scan (`--no-git --source .`).
2. A git-history scan over the pull request commit range (`--log-opts "<base>..<head>"`).

If either scan finds a potential secret, the job fails. The workflow always uploads scan artifacts:

- `gitleaks-working-tree.sarif`
- `gitleaks-history.json`

## Running the scanner locally

Run the same command style locally before pushing a branch so failures are caught early.

```bash
curl -sSL https://github.com/gitleaks/gitleaks/releases/download/v8.24.2/gitleaks_8.24.2_linux_x64.tar.gz \
  | tar -xz -C /tmp gitleaks

/tmp/gitleaks detect --no-git --source . --config .gitleaks.toml --redact --no-banner \
  --report-format sarif --report-path gitleaks-working-tree.sarif --exit-code 1

/tmp/gitleaks detect --source . --config .gitleaks.toml --redact --no-banner \
  --log-opts "origin/main..HEAD" --report-format json --report-path gitleaks-history.json --exit-code 1
```

For pull requests in CI, `origin/main..HEAD` is replaced with the exact SHA range
`<pull_request.base.sha>..<pull_request.head.sha>`.

## If a real secret is detected

1. **Rotate the secret immediately.** Regenerate the credential in the upstream system (cloud provider, payment gateway, etc.)
   so the exposed value no longer works.
2. **Remove the secret from the repository.** Replace it with configuration that reads from environment variables or another
   secure storage location. Update or purge any committed files that contained the value.
3. **Force-push only after sanitising history.** If the secret exists in previous commits on the branch, rewrite the branch
   history (for example with `git rebase -i` or `git filter-repo`) to remove the value and force-push the cleaned branch.
4. **Re-run Gitleaks locally.** Execute the commands above to confirm the issue is resolved before opening or updating the pull
   request.
5. **Notify the maintainers.** Open an issue or contact the project maintainers privately so that any deployed environments or
   dependent services using the old secret can also be rotated.

Following the steps above ensures that the automated scans remain actionable and that any genuine credential exposure is handled
quickly and safely.
