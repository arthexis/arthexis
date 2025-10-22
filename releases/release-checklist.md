# Release Checklist

1. Check version number availability
2. When bumping the minor version, run the release helper (`scripts/release.sh`) so it automatically creates a maintenance branch from the last commit of the outgoing minor release (for example, `release/v1.4`). Keep that branch for future patch releases so hotfixes can ship without picking up new features.
3. Confirm release TODO completion
4. Freeze, squash and approve migrations
5. Compose CHANGELOG and documentation
6. Execute pre-release actions
7. Build release artifacts
8. Complete test suite with --all flag
9. Get Release Manager Approval
10. Upload final build to PyPI
