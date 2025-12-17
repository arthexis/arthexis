# Secret Scanning

The CI pipeline runs automated secret scans to prevent accidental exposure of credentials. Every pull request executes a
[Gitleaks](https://github.com/gitleaks/gitleaks) scan against the committed history of the change and the working tree to catch
leaks before they land on the main branch. The scanner is configured with [`.gitleaks.toml`](https://github.com/arthexis/arthexis/blob/main/.gitleaks.toml)
so that legitimate placeholder credentials used in test fixtures are ignored while any other matches will fail the build.

## Running the scanner locally

Run the same checks locally before pushing a branch so that failures can be resolved early. The commands below download the
Gitleaks binary, scan the working tree, and then scan the commit range that would be sent to a pull request.

```bash
curl -sSL https://github.com/gitleaks/gitleaks/releases/download/v8.18.1/gitleaks_8.18.1_linux_x64.tar.gz \
  | tar -xz -C /tmp gitleaks
/tmp/gitleaks detect --no-git --source . --config .gitleaks.toml --redact --no-banner
/tmp/gitleaks detect --source . --config .gitleaks.toml --redact --no-banner \
  --log-opts "--no-merges origin/main..HEAD"
```

Adjust the branch name in the final command to match the base branch of your pull request.

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
