# Workgroup Play Password

The public Evennia play account uses a daily two-word password instead of a
fixed shared password. The current password is displayed on:

```text
https://arthexis.com/workgroup/
```

The page is linked from the public footer as `The Workgroup`.

## Configuration

Set a stable secret seed on each deployed node:

```bash
python manage.py env --set ARTHEXIS_WORKGROUP_PASSWORD_SEED '<secret-seed>'
python manage.py env --set ARTHEXIS_WORKGROUP_PASSWORD_TIMEZONE America/Monterrey
```

`ARTHEXIS_WORKGROUP_PASSWORD_SEED` must not be committed. The suite derives the
daily password from this seed and the local calendar date.

## Rotation

To inspect the current password metadata:

```bash
python manage.py workgroup_password --json
```

To set the local Unix `play` account password without echoing the password:

```bash
sudo -E python manage.py workgroup_password --apply-user play
```

Production nodes should run that command at local midnight with a systemd timer.
If the seed changes, run the command once immediately so the Unix account
matches the value published on `/workgroup/`.

To install the production timer from a deployed suite checkout:

```bash
sudo scripts/setup_workgroup_play_password.sh --apply-now
```

The setup script creates a seed when none exists, stores it in `arthexis.env`,
restricts that file to the node account, and installs
`arthexis-workgroup-play-password.timer`.
