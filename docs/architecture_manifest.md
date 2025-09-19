# Architecture manifest

The CI job that determines which node roles need to be exercised by a change now
reads `ci/architecture_manifest.yml`. The file groups reusable components by the
files that define them and links each component either to
`nodes.NodeFeature` slugs (which already carry the role assignments) or to
explicit node roles when the component is not feature-driven.

## File structure

`architecture_manifest.yml` is valid JSON (and therefore valid YAML). It has
three top-level sections:

- `shared_globs` – any path that matches one of these globs is considered shared
  infrastructure. A change touching one of these files forces the script to
  return **all** node roles so that the test matrix stays conservative. This is
  where shared settings such as `config/settings.py` live.
- `components` – each key is a reusable component. A component includes:
  - `description`: optional human readable context.
  - `paths`: the globs that identify source files and tests for the component.
  - `features`: optional list of `nodes.NodeFeature.slug` values. The script
    expands these into roles by reading the NodeFeature fixtures.
  - `roles`: optional explicit roles. Use this when the component is not tied to
    a feature (for example site fixtures that depend directly on a node role).

Any component can use `paths`, `features`, and `roles` together. If a component
lists both `features` and `roles`, the resolved role set is the union of both.

## Adding new coverage

1. Identify the reusable component and list the globs that cover its
   implementation and tests. Keep the globs specific so unrelated changes do not
   produce false positives.
2. If the component is controlled by a `NodeFeature`, add the slug to
   `features`. Otherwise list the roles explicitly.
3. Update or add fixtures under `nodes/fixtures/node_features__*.json` if new
   feature-to-role assignments are needed. The detection script automatically
   reads these fixtures.
4. Commit the manifest change with the related code. The CI script will pick up
   the new component on the next run.

When in doubt, prefer adding a component that matches too many files over
missing coverage altogether. The fallback behaviour (trigger all roles when a
change hits `shared_globs` or when no component matches) keeps the deployment
pipeline safe.
