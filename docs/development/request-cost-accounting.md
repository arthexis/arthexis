# Request cost accounting for agents

This policy explains how Arthexis agents should surface development time and resource costs for every request so product managers can see the full delivery cost of work, including work that is usually treated as invisible overhead.

## Goal

Every request should produce a summary that makes delivery cost visible enough for product planning, prioritization, and sequencing decisions.

That means agents should report not only what changed, but also what it cost to investigate, implement, validate, and prepare the change for handoff.

## Required cost summary structure

Every final summary, implementation report, or handoff should include a `Cost Summary` section with the following headings:

### Time cost

Report a realistic estimate for time already spent, plus any notable remaining effort.

Recommended format:

- `Spent`: `~30-45 minutes`
- `Remaining`: `~15-30 minutes` or `none identified`

### Resource cost

List the practical resources consumed by the request, such as:

- engineering attention
- environment bootstrapping
- test execution time
- preview generation
- migrations or fixture churn
- CI usage
- external API calls or service dependencies
- hardware access or operational coordination

Use simple labels like `low`, `medium`, or `high` when exact numerical pricing would be speculative.

### Cost drivers

Explain why the work cost what it did. Common drivers include:

- unfamiliar or distributed code paths
- flaky tests or repeated retries
- setup requirements
- hidden dependency changes
- preview or admin validation
- migrations or data backfills
- documentation updates

### Product signal

Summarize what product managers should learn from the cost profile. Examples:

- a small feature request actually has moderate operational overhead
- repeated setup friction suggests platform investment is worthwhile
- the request is cheap now because groundwork already exists
- the work is blocked more by coordination than implementation complexity

## Estimation rules

- Be explicit when a value is an estimate.
- Prefer ranges over false precision.
- Include invisible work such as reading context, reproducing issues, validating migrations, or preparing screenshots.
- If the request is analysis-only, report analysis and coordination costs rather than omitting the section.
- If the request appears cheap but depends on scarce skills or privileged environments, mention that explicitly.

## Example summary block

```markdown
## Cost Summary
- **Time cost:** spent `~45-60 minutes`; remaining `~30 minutes` for production validation.
- **Resource cost:** medium engineering effort, low runtime cost, one preview capture, and reviewer attention for admin workflow confirmation.
- **Cost drivers:** distributed Django admin wiring, environment refresh, and targeted regression testing.
- **Product signal:** this is a small UI adjustment with disproportionately high validation cost, so batching similar admin changes would reduce overhead.
```

## Suggested implementation habit

When a request starts, agents should maintain rough notes for:

- discovery time
- implementation time
- validation time
- blocked or retried work
- external dependencies

Those notes do not need to be perfect, but they should be good enough to make the final summary honest and useful.
