## Why runserver output appears twice in VS Code PowerShell

When launching `manage.py runserver` through VS Code's Python debugger on
Windows, the console often shows each line twice. This happens because
`manage.py` starts **two processes** whenever Django's autoreloader is
enabled (the default in development):

1. The debugger first launches the *wrapper* process that watches the
   filesystem for changes.
2. The wrapper then starts a *child* process that runs the actual server.

Both processes print their startup diagnostics (e.g., "No changes detected"
and migration checks), so you see duplicated lines before the server begins
serving requests. Once the server is running, only the child process keeps
printing new logs.

To avoid the duplication, disable the autoreloader when debugging:

```powershell
python manage.py runserver --noreload
```

You can also set `DJANGO_SUPPRESS_MIGRATION_CHECK=1` if you only want to
silence the repeated migration output while keeping the autoreloader active.

### Running startup checks only once

Django's autoreloader runs your module imports twice (once in the watcher and
once in the child server). If you need certain initialization to happen only
once, run it *before* `runserver` starts or gate it to the child process:

* Add a pre-flight step in every launcher (PowerShell, Bash, VS Code) that runs
  the expensive checks **once**, then skip them in the subsequent server start:

  ```powershell
  python manage.py migrate --check
  $env:DJANGO_SUPPRESS_MIGRATION_CHECK = "1"
  python manage.py runserver
  ```

  ```bash
  python manage.py migrate --check
  export DJANGO_SUPPRESS_MIGRATION_CHECK=1
  python manage.py runserver
  ```

  The first command ensures migrations are verified a single time; the
  environment variable suppresses the duplicated check when the watcher forks
  the child server.

* For app-level initialization that runs during import or `AppConfig.ready`,
  guard it so it only executes in the child server process:

  ```python
  import os

  def ready(self):
      if os.environ.get("RUN_MAIN") != "true":
          return  # skip watcher process
      perform_expensive_setup()
  ```

These patterns prevent double execution while keeping the autoreloader intact
for code reloads during development.
