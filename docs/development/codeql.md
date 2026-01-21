# CodeQL configuration

Arthexis uses an advanced CodeQL workflow with a dedicated configuration file at `.github/codeql/codeql-config.yml`. GitHub's default setup is intentionally not used because it does not let us tune scan scope. The configuration keeps CodeQL focused on application logic while avoiding noisy, non-actionable results.

## Why specific paths are excluded

The `paths-ignore` list is intentionally small and only targets directories that are either generated or not part of the runtime Python surface area.

| Path | Why it is excluded |
| --- | --- |
| `tests/**` | Test suites and fixtures include mocks, synthetic payloads, and intentional edge cases that generate false positives and do not ship to production. |
| `docs/**` | Documentation content (including mkdocs assets) is not executed by the application. |
| `static/**` | Front-end bundles, static images, and vendor assets are not Python source and are typically built artifacts. |
| `media/**` | User-uploaded media is runtime data, not source code. |
| `scripts/**` | Local maintenance scripts are not part of the deployed service surface and tend to include one-off automation. |
| `.github/workflows/**` | Workflow YAML does not participate in runtime behavior; keeping the ignore narrow avoids masking other `.github` automation such as actions in the future. |
| `**/migrations/**` | Framework migrations are generated snapshots; findings here are rarely actionable compared to the model code that produced them. |

## Making changes

If you add new non-production assets that create scan noise, update `.github/codeql/codeql-config.yml` and document the rationale here. If you are unsure, default to **not** excluding directories that contain runtime application code.
