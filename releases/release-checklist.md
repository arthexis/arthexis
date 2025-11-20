# Release Checklist

1. Check version number availability
2. When bumping the minor version, run the release helper (`scripts/release.sh`) so it automatically creates a maintenance branch from the last commit of the outgoing minor release (for example, `release/v1.4`). Keep that branch for future patch releases so hotfixes can ship without picking up new features.
3. Freeze, squash and approve migrations
4. Compose CHANGELOG and documentation
5. Execute pre-release actions
6. Build release artifacts
7. Complete test suite with --all flag
8. Get Release Manager Approval
9. Upload final build to PyPI
