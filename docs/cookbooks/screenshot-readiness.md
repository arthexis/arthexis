# Screenshot readiness checklist

Use this checklist to stabilise UI screenshot runs when reviewing changes. It keeps
Django installed, enables the `screenshot-poll` feature on the local Terminal
node, and fails fast when prerequisites are missing.

## Automation bootstrap (preferred)

Use the lightweight Terminal profile to prepare the environment before any
screenshot command:

```bash
./scripts/ci/bootstrap_screenshots.sh
```

The helper performs the following steps:

1. Runs `env-refresh.sh --clean` so the virtualenv is rebuilt and Django is
   installed from `requirements.txt`.
2. Verifies Django is importable; if it is missing, re-run `./install.sh --terminal --no-start`.
3. Registers the local node and enables the `screenshot-poll` feature for the
   Terminal role via `manage.py prepare_screenshot_feature`.

## Fast start for reviewers

When you only need a live server for screenshots, use the Terminal role and
avoid heavier installs:

```bash
./scripts/ci/bootstrap_screenshots.sh && ./install.sh --terminal --no-start && ./start.sh --silent
```

- The bootstrap step ensures Django and the screenshot feature are available.
- `install.sh --terminal --no-start` keeps the node lightweight while preparing
  assets.
- `start.sh --silent` brings up the services without chatty logs.

## Eligibility checks

- The `prepare_screenshot_feature` management command links the `screenshot-poll`
  feature to the local node and reruns the same eligibility logic as the admin
  action.
- In the Django admin, you can also run **Check features for eligibility** on the
  Screenshot Poll feature to verify it is ready; the command above performs the
  same check without needing the UI.

## Fail-fast cues

- If `scripts/ci/run_screenshots.py` reports missing Django, rerun the bootstrap
  helper or `./install.sh --terminal --no-start` to reinstall dependencies.
- Before running screenshot specs manually, verify the interpreter with:

  ```bash
  ./.venv/bin/python -m django --version
  ```

  If the command fails, the bootstrap helper will reinstall the required
  packages.
