# Agent working notes

## Preview command

For UI/admin preview capture during development, use:

1. `python manage.py migrate`
2. `python manage.py runserver 0.0.0.0:<port> --noreload`
3. `python manage.py preview --base-url http://127.0.0.1:<port> --path /admin/ --output media/previews/admin-preview.png`

The command auto-ensures deterministic admin credentials (`admin` / `admin123`), tries Chromium then Firefox by default, and prints image diagnostics including `mostly_white`.
