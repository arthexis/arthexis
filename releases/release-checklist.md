# Release Checklist

1. Ensure all tests pass.
2. Generate `CHANGELOG.rst` from commit messages using `scripts/generate-changelog.sh`.
3. Run `scripts/release.sh <version>` to:
   - bump the version number
   - freeze the current migrations
   - create a source archive for the release
   - commit changes and tag the release
