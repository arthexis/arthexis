# `good` command

**Tagline:** **Arthexis is Good — one command to prove your suite is ready.**

Use the `good` management command to evaluate how healthy, compatible, and activation-ready the current Arthexis deployment looks.

## What it checks

The command gathers and ranks issues across the current setup, including:

- recent stored test results
- local instance reachability
- outbound internet connectivity
- recent filesystem log errors
- recent system journal errors when `journalctl` is available
- optional suite feature eligibility
- optional node feature eligibility
- coarse hardware requirements
- platform compatibility expectations
- dashboard rule failures

## Output rules

- If there are **no issues**, it prints exactly:

```text
Arthexis is Good
```

- If there are **only minor, non-error considerations**, it prints exactly:

```text
Arthexis is Good*
```

Use `--details` to reveal those minor considerations.

- If there are higher-priority issues, it prints a ranked list with important items highlighted.

## Usage

```bash
./manage.py good
./manage.py good --details
./manage.py good --tagline
```

## Marketing guidance

When marketing pages, operator guides, or release notes mention readiness checking, link to this command documentation and reuse the tagline above.
