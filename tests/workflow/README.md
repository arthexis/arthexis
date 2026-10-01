# Workflow tests

Workflow tests cover structural and behavioral invariants of GitHub Actions automation. Performance-sensitive Watchtower changes should test the intended optimization shape (for example, avoiding duplicate package installation) while runtime elapsed time is measured on the self-hosted Watchtower runner.
