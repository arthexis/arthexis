import base64
import binascii
import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone

import requests
from django.conf import settings
from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import REDIRECT_FIELD_NAME, authenticate, login
from django.contrib import messages
from django.contrib.sites.models import Site
from django.http import Http404, JsonResponse, HttpResponse
from django.shortcuts import redirect, render, resolve_url
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from django.urls import NoReverseMatch, reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.utils.http import url_has_allowed_host_and_scheme
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import errno
import subprocess
from typing import Optional, Sequence

from django.template.loader import get_template
from django.test import signals

from utils import revision
from nodes.utils import save_screenshot
from utils.api import api_login_required

logger = logging.getLogger(__name__)

PYPI_REQUEST_TIMEOUT = 10

from . import changelog as changelog_utils
from . import temp_passwords
from .models import OdooProfile, Product, CustomerAccount, PackageRelease, Todo
from .models import RFID


@staff_member_required
def odoo_products(request):
    """Return available products from the user's Odoo instance."""

    profile = getattr(request.user, "odoo_profile", None)
    if not profile or not profile.is_verified:
        raise Http404
    try:
        products = profile.execute(
            "product.product",
            "search_read",
            fields=["name"],
            limit=50,
        )
    except Exception:
        logger.exception(
            "Failed to fetch Odoo products via API for user %s (profile_id=%s, host=%s, database=%s)",
            getattr(request.user, "pk", None),
            getattr(profile, "pk", None),
            getattr(profile, "host", None),
            getattr(profile, "database", None),
        )
        return JsonResponse({"detail": "Unable to fetch products"}, status=502)
    items = [{"id": p.get("id"), "name": p.get("name", "")} for p in products]
    return JsonResponse(items, safe=False)


@staff_member_required
def odoo_quote_report(request):
    """Display a consolidated quote report from the user's Odoo instance."""

    profile = getattr(request.user, "odoo_profile", None)
    context = {
        "title": _("Quote Report"),
        "profile": profile,
        "error": None,
        "template_stats": [],
        "quotes": [],
        "recent_products": [],
        "installed_modules": [],
        "profile_url": "",
    }

    profile_admin = admin_site._registry.get(OdooProfile)
    if profile_admin is not None:
        try:
            context["profile_url"] = profile_admin.get_my_profile_url(request)
        except Exception:  # pragma: no cover - defensive fallback
            context["profile_url"] = ""

    if not profile or not profile.is_verified:
        context["error"] = _(
            "Configure and verify your CRM employee credentials before generating the report."
        )
        return TemplateResponse(
            request, "admin/core/odoo_quote_report.html", context
        )

    def _parse_datetime(value):
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value)
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                text_iso = text.replace(" ", "T")
                try:
                    dt = datetime.fromisoformat(text_iso)
                except ValueError:
                    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                        try:
                            dt = datetime.strptime(text, fmt)
                            break
                        except ValueError:
                            continue
                    else:
                        return None
        if timezone.is_naive(dt):
            tzinfo = getattr(timezone, "utc", datetime_timezone.utc)
            dt = timezone.make_aware(dt, tzinfo)
        return dt

    try:
        templates = profile.execute(
            "sale.order.template",
            "search_read",
            fields=["name"],
            order="name asc",
        )
        template_usage = profile.execute(
            "sale.order",
            "read_group",
            [[("sale_order_template_id", "!=", False)]],
            ["sale_order_template_id"],
            lazy=False,
        )

        usage_map = {}
        for entry in template_usage:
            template_info = entry.get("sale_order_template_id")
            if not template_info:
                continue
            template_id = template_info[0]
            usage_map[template_id] = entry.get(
                "sale_order_template_id_count", 0
            )

        context["template_stats"] = [
            {
                "id": template.get("id"),
                "name": template.get("name", ""),
                "quote_count": usage_map.get(template.get("id"), 0),
            }
            for template in templates
        ]

        ninety_days_ago = timezone.now() - timedelta(days=90)
        quotes = profile.execute(
            "sale.order",
            "search_read",
            [
                [
                    ("create_date", ">=", ninety_days_ago.strftime("%Y-%m-%d %H:%M:%S")),
                    ("state", "!=", "cancel"),
                    ("quote_sent", "=", False),
                ]
            ],
            fields=[
                "name",
                "amount_total",
                "partner_id",
                "activity_type_id",
                "activity_summary",
                "tag_ids",
                "create_date",
                "currency_id",
            ],
            order="create_date desc",
        )

        tag_ids = set()
        currency_ids = set()
        for quote in quotes:
            tag_ids.update(quote.get("tag_ids") or [])
            currency_info = quote.get("currency_id")
            if (
                isinstance(currency_info, (list, tuple))
                and len(currency_info) >= 1
                and currency_info[0]
            ):
                currency_ids.add(currency_info[0])

        tag_map: dict[int, str] = {}
        if tag_ids:
            tag_records = profile.execute(
                "sale.order.tag",
                "read",
                list(tag_ids),
                fields=["name"],
            )
            for tag in tag_records:
                tag_id = tag.get("id")
                if tag_id is not None:
                    tag_map[tag_id] = tag.get("name", "")

        currency_map: dict[int, dict[str, str]] = {}
        if currency_ids:
            currency_records = profile.execute(
                "res.currency",
                "read",
                list(currency_ids),
                fields=["name", "symbol"],
            )
            for currency in currency_records:
                currency_id = currency.get("id")
                if currency_id is not None:
                    currency_map[currency_id] = {
                        "name": currency.get("name", ""),
                        "symbol": currency.get("symbol", ""),
                    }

        prepared_quotes = []
        for quote in quotes:
            partner = quote.get("partner_id")
            customer = ""
            if isinstance(partner, (list, tuple)) and len(partner) >= 2:
                customer = partner[1]

            activity_type = quote.get("activity_type_id")
            activity_name = ""
            if isinstance(activity_type, (list, tuple)) and len(activity_type) >= 2:
                activity_name = activity_type[1]

            activity_summary = quote.get("activity_summary") or ""
            activity_value = activity_summary or activity_name

            quote_tags = [
                tag_map.get(tag_id, str(tag_id))
                for tag_id in quote.get("tag_ids") or []
            ]

            currency_info = quote.get("currency_id")
            currency_label = ""
            if isinstance(currency_info, (list, tuple)) and currency_info:
                currency_id = currency_info[0]
                currency_details = currency_map.get(currency_id, {})
                currency_label = (
                    currency_details.get("symbol")
                    or currency_details.get("name")
                    or (currency_info[1] if len(currency_info) >= 2 else "")
                )

            amount_total = quote.get("amount_total") or 0
            if currency_label:
                total_display = f"{currency_label}{amount_total:,.2f}"
            else:
                total_display = f"{amount_total:,.2f}"

            prepared_quotes.append(
                {
                    "name": quote.get("name", ""),
                    "customer": customer,
                    "activity": activity_value,
                    "tags": quote_tags,
                    "create_date": _parse_datetime(quote.get("create_date")),
                    "total": amount_total,
                    "total_display": total_display,
                }
            )

        context["quotes"] = prepared_quotes

        products = profile.execute(
            "product.product",
            "search_read",
            fields=["name", "default_code", "write_date", "create_date"],
            limit=10,
            order="write_date desc, create_date desc",
        )
        context["recent_products"] = [
            {
                "name": product.get("name", ""),
                "default_code": product.get("default_code", ""),
                "create_date": _parse_datetime(product.get("create_date")),
                "write_date": _parse_datetime(product.get("write_date")),
            }
            for product in products
        ]

        modules = profile.execute(
            "ir.module.module",
            "search_read",
            [[("state", "=", "installed")]],
            fields=["name", "shortdesc", "latest_version", "author"],
            order="name asc",
        )
        context["installed_modules"] = [
            {
                "name": module.get("name", ""),
                "shortdesc": module.get("shortdesc", ""),
                "latest_version": module.get("latest_version", ""),
                "author": module.get("author", ""),
            }
            for module in modules
        ]

    except Exception:
        logger.exception(
            "Failed to build Odoo quote report for user %s (profile_id=%s)",
            getattr(request.user, "pk", None),
            getattr(profile, "pk", None),
        )
        context["error"] = _("Unable to generate the quote report from Odoo.")
        return TemplateResponse(
            request,
            "admin/core/odoo_quote_report.html",
            context,
            status=502,
        )

    return TemplateResponse(request, "admin/core/odoo_quote_report.html", context)


@staff_member_required
@require_GET
def request_temp_password(request):
    """Generate a temporary password for the authenticated staff member."""

    user = request.user
    username = user.get_username()
    password = temp_passwords.generate_password()
    entry = temp_passwords.store_temp_password(
        username,
        password,
        allow_change=True,
    )
    context = {
        **admin_site.each_context(request),
        "title": _("Temporary password"),
        "username": username,
        "password": password,
        "expires_at": timezone.localtime(entry.expires_at),
        "allow_change": entry.allow_change,
        "return_url": reverse("admin:password_change"),
    }
    return TemplateResponse(
        request,
        "admin/core/request_temp_password.html",
        context,
    )


@staff_member_required
@require_GET
def version_info(request):
    """Return the running application version and Git revision."""

    version = ""
    version_path = Path(settings.BASE_DIR) / "VERSION"
    if version_path.exists():
        version = version_path.read_text(encoding="utf-8").strip()
    return JsonResponse(
        {
            "version": version,
            "revision": revision.get_revision(),
        }
    )


from . import release as release_utils
from .log_paths import select_log_dir


TODO_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


DIRTY_COMMIT_DEFAULT_MESSAGE = "chore: commit pending changes"


DIRTY_STATUS_LABELS = {
    "A": _("Added"),
    "C": _("Copied"),
    "D": _("Deleted"),
    "M": _("Modified"),
    "R": _("Renamed"),
    "U": _("Updated"),
    "??": _("Untracked"),
}


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def _release_log_name(package_name: str, version: str) -> str:
    return f"pr.{package_name}.v{version}.log"


def _ensure_log_directory(path: Path) -> tuple[bool, OSError | None]:
    """Return whether ``path`` is writable along with the triggering error."""

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, exc

    probe = path / f".permcheck_{uuid.uuid4().hex}"
    try:
        with probe.open("w", encoding="utf-8") as fh:
            fh.write("")
    except OSError as exc:
        return False, exc
    else:
        try:
            probe.unlink()
        except OSError:
            pass
        return True, None


def _resolve_release_log_dir(preferred: Path) -> tuple[Path, str | None]:
    """Return a writable log directory for the release publish flow."""

    writable, error = _ensure_log_directory(preferred)
    if writable:
        return preferred, None

    logger.warning(
        "Release log directory %s is not writable: %s", preferred, error
    )

    env_override = os.environ.pop("ARTHEXIS_LOG_DIR", None)
    fallback = select_log_dir(Path(settings.BASE_DIR))
    if env_override is not None:
        if Path(env_override) == fallback:
            os.environ["ARTHEXIS_LOG_DIR"] = env_override
        else:
            os.environ["ARTHEXIS_LOG_DIR"] = str(fallback)

    if fallback == preferred:
        if error:
            raise error
        raise PermissionError(f"Release log directory {preferred} is not writable")

    fallback_writable, fallback_error = _ensure_log_directory(fallback)
    if not fallback_writable:
        raise fallback_error or PermissionError(
            f"Release log directory {fallback} is not writable"
        )

    settings.LOG_DIR = fallback
    warning = (
        f"Release log directory {preferred} is not writable; using {fallback}"
    )
    logger.warning(warning)
    return fallback, warning


def _commit_todo_fixtures_if_needed(log_path: Path) -> None:
    """Stage and commit modified TODO fixtures before syncing with origin/main."""

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return

    todo_paths: set[Path] = set()
    for line in (status.stdout or "").splitlines():
        if not line:
            continue
        path_fragment = line[3:].strip()
        if "->" in path_fragment:
            path_fragment = path_fragment.split("->", 1)[1].strip()
        path = Path(path_fragment)
        if path.suffix == ".json" and path.parent == Path("core/fixtures"):
            if path.name.startswith("todo__"):
                todo_paths.add(path)

    if not todo_paths:
        return

    sorted_paths = sorted(todo_paths)
    subprocess.run(
        ["git", "add", *[str(path) for path in sorted_paths]],
        check=True,
    )
    formatted = ", ".join(_format_path(path) for path in sorted_paths)
    _append_log(log_path, f"Staged TODO fixtures {formatted}")

    diff = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", *[str(path) for path in sorted_paths]],
        check=True,
        capture_output=True,
        text=True,
    )

    if (diff.stdout or "").strip():
        message = "chore: update TODO fixtures"
        subprocess.run(["git", "commit", "-m", message], check=True)
        _append_log(log_path, f"Committed TODO fixtures ({message})")


def _sync_with_origin_main(log_path: Path) -> None:
    """Ensure the current branch is rebased onto ``origin/main``."""

    _commit_todo_fixtures_if_needed(log_path)

    if not _has_remote("origin"):
        _append_log(log_path, "No git remote configured; skipping sync with origin/main")
        return

    try:
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        _append_log(log_path, "Fetched latest changes from origin/main")
        subprocess.run(["git", "rebase", "origin/main"], check=True)
        _append_log(log_path, "Rebased current branch onto origin/main")
    except subprocess.CalledProcessError as exc:
        subprocess.run(["git", "rebase", "--abort"], check=False)
        _append_log(log_path, "Rebase onto origin/main failed; aborted rebase")

        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        if stdout:
            _append_log(log_path, "git output:\n" + stdout)
        if stderr:
            _append_log(log_path, "git errors:\n" + stderr)

        status = subprocess.run(
            ["git", "status"], capture_output=True, text=True, check=False
        )
        status_output = (status.stdout or "").strip()
        status_errors = (status.stderr or "").strip()
        if status_output:
            _append_log(log_path, "git status:\n" + status_output)
        if status_errors:
            _append_log(log_path, "git status errors:\n" + status_errors)

        branch = _current_branch() or "(detached HEAD)"
        instructions = [
            "Manual intervention required to finish syncing with origin/main.",
            "Ensure you are on the branch you intend to publish (normally `main`; currently "
            f"{branch}).",
            "Then run these commands from the repository root:",
            "  git fetch origin main",
            "  git rebase origin/main",
            "Resolve any conflicts (use `git status` to review files) and continue the rebase.",
        ]

        if branch != "main" and branch != "(detached HEAD)":
            instructions.append(
                "If this branch should mirror main, push the rebased changes with "
                f"`git push origin {branch}:main`."
            )
        else:
            instructions.append("Push the rebased branch with `git push origin main`.")

        instructions.append(
            "If push authentication fails, verify your git remote permissions and SSH keys "
            "for origin/main before retrying the publish flow."
        )
        _append_log(log_path, "\n".join(instructions))

        raise Exception("Rebase onto main failed") from exc


def _clean_repo() -> None:
    """Return the git repository to a clean state."""
    subprocess.run(["git", "reset", "--hard"], check=False)
    subprocess.run(["git", "clean", "-fd"], check=False)


def _format_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _git_stdout(args: Sequence[str]) -> str:
    proc = subprocess.run(args, check=True, capture_output=True, text=True)
    return (proc.stdout or "").strip()


def _has_remote(remote: str) -> bool:
    proc = subprocess.run(
        ["git", "remote"],
        check=True,
        capture_output=True,
        text=True,
    )
    remotes = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return remote in remotes


def _current_branch() -> str | None:
    branch = _git_stdout(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch == "HEAD":
        return None
    return branch


def _has_upstream(branch: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _collect_dirty_files() -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    dirty: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        status_code = line[:2]
        status = status_code.strip() or status_code
        path = line[3:]
        dirty.append(
            {
                "path": path,
                "status": status,
                "status_label": DIRTY_STATUS_LABELS.get(status, status),
            }
        )
    return dirty


def _format_subprocess_error(exc: subprocess.CalledProcessError) -> str:
    return (exc.stderr or exc.stdout or str(exc)).strip() or str(exc)


def _git_authentication_missing(exc: subprocess.CalledProcessError) -> bool:
    message = (exc.stderr or exc.stdout or "").strip().lower()
    if not message:
        return False
    auth_markers = [
        "could not read username",
        "authentication failed",
        "fatal: authentication failed",
        "terminal prompts disabled",
    ]
    return any(marker in message for marker in auth_markers)


def _push_release_changes(log_path: Path) -> bool:
    """Push release commits to ``origin`` and log the outcome."""

    if not _has_remote("origin"):
        _append_log(
            log_path, "No git remote configured; skipping push of release changes"
        )
        return False

    try:
        branch = _current_branch()
        if branch is None:
            push_cmd = ["git", "push", "origin", "HEAD"]
        elif _has_upstream(branch):
            push_cmd = ["git", "push"]
        else:
            push_cmd = ["git", "push", "--set-upstream", "origin", branch]
        subprocess.run(push_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        details = _format_subprocess_error(exc)
        if _git_authentication_missing(exc):
            _append_log(
                log_path,
                "Authentication is required to push release changes to origin; skipping push",
            )
            if details:
                _append_log(log_path, details)
            return False
        _append_log(
            log_path, f"Failed to push release changes to origin: {details}"
        )
        raise Exception("Failed to push release changes") from exc

    _append_log(log_path, "Pushed release changes to origin")
    return True


def _ensure_origin_main_unchanged(log_path: Path) -> None:
    """Verify that ``origin/main`` has not advanced during the release."""

    if not _has_remote("origin"):
        _append_log(
            log_path, "No git remote configured; skipping origin/main verification"
        )
        return

    try:
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        _append_log(log_path, "Fetched latest changes from origin/main")
        origin_main = _git_stdout(["git", "rev-parse", "origin/main"])
        merge_base = _git_stdout(["git", "merge-base", "HEAD", "origin/main"])
    except subprocess.CalledProcessError as exc:
        details = (getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)).strip()
        if details:
            _append_log(log_path, f"Failed to verify origin/main status: {details}")
        else:  # pragma: no cover - defensive fallback
            _append_log(log_path, "Failed to verify origin/main status")
        raise Exception("Unable to verify origin/main status") from exc

    if origin_main != merge_base:
        _append_log(log_path, "origin/main advanced during release; restart required")
        raise Exception("origin/main changed during release; restart required")

    _append_log(log_path, "origin/main unchanged since last sync")


def _next_patch_version(version: str) -> str:
    from packaging.version import InvalidVersion, Version

    cleaned = version.rstrip("+")
    try:
        parsed = Version(cleaned)
    except InvalidVersion:
        parts = cleaned.split(".") if cleaned else []
        for index in range(len(parts) - 1, -1, -1):
            segment = parts[index]
            if segment.isdigit():
                parts[index] = str(int(segment) + 1)
                return ".".join(parts)
        return cleaned or version
    return f"{parsed.major}.{parsed.minor}.{parsed.micro + 1}"


def _should_use_python_changelog(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror in {193}:
        return True
    return exc.errno in {errno.ENOEXEC, errno.EACCES, errno.ENOENT}


def _generate_changelog_with_python(log_path: Path) -> None:
    _append_log(log_path, "Falling back to Python changelog generator")
    changelog_path = Path("CHANGELOG.rst")
    previous = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else None
    range_spec = changelog_utils.determine_range_spec(previous_text=previous)
    sections = changelog_utils.collect_sections(range_spec=range_spec, previous_text=previous)
    content = changelog_utils.render_changelog(sections)
    if not content.endswith("\n"):
        content += "\n"
    changelog_path.write_text(content, encoding="utf-8")
    _append_log(log_path, "Regenerated CHANGELOG.rst using Python fallback")


def _log_missing_release_instructions(log_path: Path, version: str) -> None:
    instructions = "".join(
        (
            "Manual changelog update required for v",
            f"{version}: ensure the release commit is merged into main, ",
            "then rerun `scripts/generate-changelog.sh v",
            f"{version}`. If the commit is unavailable, add the release notes ",
            "to CHANGELOG.rst manually and commit the result.",
        )
    )
    _append_log(log_path, instructions)


def _todo_blocks_publish(todo: Todo, release: PackageRelease) -> bool:
    """Return ``True`` when ``todo`` should block the release workflow."""

    if getattr(todo, "is_stale", False):
        return False

    request = (todo.request or "").strip()
    release_name = (release.package.name or "").strip()
    if not request or not release_name:
        return True

    prefix = f"create release {release_name.lower()} "
    if not request.lower().startswith(prefix):
        return True

    release_version = (release.version or "").strip()
    generated_version = (todo.generated_for_version or "").strip()
    if not release_version or release_version != generated_version:
        return True

    generated_revision = (todo.generated_for_revision or "").strip()
    release_revision = (release.revision or "").strip()
    if generated_revision and release_revision and generated_revision != release_revision:
        return True

    if not todo.is_seed_data:
        return True

    return False


def _render_release_progress_error(
    request,
    release: "PackageRelease | None",
    action: str,
    message: str,
    *,
    status: int = 200,
    debug_info: dict[str, object] | None = None,
):
    """Render a lightweight error response for the release progress view."""

    if settings.DEBUG and debug_info:
        debug_items = [
            {"name": str(name), "value": str(value)}
            for name, value in debug_info.items()
        ]
    else:
        debug_items = []

    release_url = ""
    if release is not None:
        try:
            release_url = reverse("admin:core_packagerelease_change", args=[release.pk])
        except NoReverseMatch:  # pragma: no cover - defensive fallback
            release_url = ""

    template = get_template("core/release_progress_error.html")
    context = {
        "release": release,
        "action": action,
        "message": message,
        "debug_items": debug_items,
        "show_debug": bool(debug_items),
        "release_url": release_url,
    }
    content = template.render(context, request)
    signals.template_rendered.send(
        sender=template.__class__,
        template=template,
        context=context,
        using=getattr(getattr(template, "engine", None), "name", None),
    )
    response = HttpResponse(content, status=status)
    response.context = context
    response.templates = [template]
    return response


def _sync_release_with_revision(release: PackageRelease) -> tuple[bool, str]:
    """Ensure ``release`` matches the repository revision and version.

    Returns a tuple ``(updated, previous_version)`` where ``updated`` is
    ``True`` when any field changed and ``previous_version`` is the version
    before synchronization.
    """

    from packaging.version import InvalidVersion, Version

    previous_version = release.version
    updated_fields: set[str] = set()

    repo_version: Version | None = None
    version_path = Path("VERSION")
    if version_path.exists():
        try:
            raw_version = version_path.read_text(encoding="utf-8").strip()
            cleaned_version = raw_version.rstrip("+") or "0.0.0"
            repo_version = Version(cleaned_version)
        except InvalidVersion:
            repo_version = None

    try:
        release_version = Version(release.version)
    except InvalidVersion:
        release_version = None

    if repo_version is not None:
        bumped_repo_version = Version(
            f"{repo_version.major}.{repo_version.minor}.{repo_version.micro + 1}"
        )
        if release_version is None or release_version < bumped_repo_version:
            release.version = str(bumped_repo_version)
            release_version = bumped_repo_version
            updated_fields.add("version")

    current_revision = revision.get_revision()
    if current_revision and current_revision != release.revision:
        release.revision = current_revision
        updated_fields.add("revision")

    if updated_fields:
        release.save(update_fields=list(updated_fields))
        PackageRelease.dump_fixture()

    package_updated = False
    if release.package_id and not release.package.is_active:
        release.package.is_active = True
        release.package.save(update_fields=["is_active"])
        package_updated = True

    version_updated = False
    if release.version:
        current = ""
        if version_path.exists():
            current = version_path.read_text(encoding="utf-8").strip()
        if current != release.version:
            version_path.write_text(f"{release.version}\n", encoding="utf-8")
            version_updated = True

    return bool(updated_fields or version_updated or package_updated), previous_version


def _changelog_notes(version: str) -> str:
    path = Path("CHANGELOG.rst")
    if not path.exists():
        return ""
    notes = changelog_utils.extract_release_notes(
        path.read_text(encoding="utf-8"), version
    )
    return notes.strip()


class PendingTodos(Exception):
    """Raised when TODO items require acknowledgment before proceeding."""


class ApprovalRequired(Exception):
    """Raised when release manager approval is required before continuing."""


class DirtyRepository(Exception):
    """Raised when the Git workspace has uncommitted changes."""


def _format_condition_failure(todo: Todo, result) -> str:
    """Return a localized error message for a failed TODO condition."""

    if result.error and result.resolved:
        detail = _("%(condition)s (error: %(error)s)") % {
            "condition": result.resolved,
            "error": result.error,
        }
    elif result.error:
        detail = _("Error: %(error)s") % {"error": result.error}
    elif result.resolved:
        detail = result.resolved
    else:
        detail = _("Condition evaluated to False")
    return _("Condition failed for %(todo)s: %(detail)s") % {
        "todo": todo.request,
        "detail": detail,
    }


def _get_return_url(request) -> str:
    """Return a safe URL to redirect back to after completing a TODO."""

    candidates = [request.GET.get("next"), request.POST.get("next")]
    referer = request.META.get("HTTP_REFERER")
    if referer:
        candidates.append(referer)

    for candidate in candidates:
        if not candidate:
            continue
        if url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return candidate
    return resolve_url("admin:index")


def _refresh_changelog_once(ctx, log_path: Path) -> None:
    """Regenerate the changelog a single time per release run."""

    if ctx.get("changelog_refreshed"):
        return

    _append_log(log_path, "Refreshing changelog before TODO review")
    try:
        subprocess.run(["scripts/generate-changelog.sh"], check=True)
    except OSError as exc:
        if _should_use_python_changelog(exc):
            _append_log(
                log_path,
                f"scripts/generate-changelog.sh failed: {exc}",
            )
            _generate_changelog_with_python(log_path)
        else:  # pragma: no cover - unexpected OSError
            raise
    else:
        _append_log(
            log_path,
            "Regenerated CHANGELOG.rst using scripts/generate-changelog.sh",
        )

    changelog_path = Path("CHANGELOG.rst")
    if changelog_path.exists():
        latest_version = changelog_utils.latest_release_version_from_history()
        if latest_version:
            text = changelog_path.read_text(encoding="utf-8")
            if not changelog_utils.changelog_has_release_section(text, latest_version):
                _append_log(
                    log_path,
                    f"Changelog missing latest release v{latest_version}",
                )
                if not ctx.get("changelog_python_retry"):
                    ctx["changelog_python_retry"] = True
                    _append_log(
                        log_path,
                        f"Retrying changelog generation for v{latest_version} using Python fallback",
                    )
                    _generate_changelog_with_python(log_path)
                    text = changelog_path.read_text(encoding="utf-8")
                    if changelog_utils.changelog_has_release_section(
                        text, latest_version
                    ):
                        _append_log(
                            log_path,
                            f"Recovered changelog entry for v{latest_version} after fallback",
                        )
                        ctx.pop("changelog_python_retry", None)
                    else:
                        _log_missing_release_instructions(log_path, latest_version)
                        raise RuntimeError(
                            _(
                                "The changelog is missing notes for the latest release (v%(version)s). "
                                "Ensure the release commit has been merged and regenerate the changelog."
                            )
                            % {"version": latest_version}
                        )
                else:
                    _log_missing_release_instructions(log_path, latest_version)
                    raise RuntimeError(
                        _(
                            "The changelog is missing notes for the latest release (v%(version)s). "
                            "Ensure the release commit has been merged and regenerate the changelog."
                        )
                        % {"version": latest_version}
                    )

    staged_paths: list[str] = []
    if changelog_path.exists():
        staged_paths.append(str(changelog_path))

    release_fixtures = sorted(Path("core/fixtures").glob("releases__*.json"))
    staged_paths.extend(str(path) for path in release_fixtures)

    if staged_paths:
        subprocess.run(["git", "add", *staged_paths], check=True)

    diff = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    changed_paths = [line.strip() for line in diff.stdout.splitlines() if line.strip()]

    if changed_paths:
        changelog_dirty = "CHANGELOG.rst" in changed_paths
        fixtures_dirty = any(path.startswith("core/fixtures/") for path in changed_paths)
        if changelog_dirty and fixtures_dirty:
            message = "chore: sync release fixtures and changelog"
        elif changelog_dirty:
            message = "docs: refresh changelog"
        else:
            message = "chore: update release fixtures"
        subprocess.run(["git", "commit", "-m", message], check=True)
        _append_log(log_path, f"Committed changelog refresh ({message})")
    else:
        _append_log(log_path, "Changelog already up to date")

    ctx["changelog_refreshed"] = True


def _purge_release_todos(log_path: Path, *, reason: str | None = None) -> int:
    """Delete active TODOs and remove generated fixture files."""

    removed = list(Todo.objects.filter(is_deleted=False))
    removed_count = len(removed)
    for todo in removed:
        todo.delete()

    if removed_count:
        summary = f"Removed {removed_count} TODO"
        if removed_count != 1:
            summary += "s"
        if reason:
            summary += f" ({reason})"
        _append_log(log_path, summary)
    elif reason:
        _append_log(log_path, f"No TODOs required removal ({reason})")

    removed_fixtures: list[Path] = []
    for path in TODO_FIXTURE_DIR.glob("todos__*.json"):
        removed_fixtures.append(path)
        path.unlink()

    if removed_fixtures:
        formatted = ", ".join(_format_path(path) for path in removed_fixtures)
        subprocess.run(
            ["git", "add", *[str(path) for path in removed_fixtures]],
            check=False,
        )
        subprocess.run(
            ["git", "commit", "-m", "chore: remove TODO fixtures"],
            check=False,
        )
        _append_log(log_path, f"Removed TODO fixtures {formatted}")

    return removed_count


def _step_check_todos(release, ctx, log_path: Path, *, user=None) -> None:
    _refresh_changelog_once(ctx, log_path)

    pending_items = Todo.refresh_active()
    pending_values = [
        {
            "id": todo.pk,
            "request": todo.request,
            "url": todo.url,
            "request_details": todo.request_details,
        }
        for todo in pending_items
    ]
    if not pending_values:
        ctx["todos_ack"] = True

    if not ctx.get("todos_ack"):
        if not ctx.get("todos_block_logged"):
            _append_log(
                log_path,
                "Release checklist requires acknowledgment before continuing. "
                "Review outstanding TODO items and confirm the checklist; "
                "publishing will resume automatically afterward.",
            )
            ctx["todos_block_logged"] = True
        ctx["todos"] = pending_values
        ctx["todos_required"] = True
        raise PendingTodos()
    _purge_release_todos(log_path, reason="release checklist acknowledged")
    ctx.pop("todos", None)
    ctx.pop("todos_required", None)
    ctx["todos_ack"] = True


def _major_minor_version_changed(previous: str, current: str) -> bool:
    """Return ``True`` when the version bump changes major or minor."""

    previous_clean = (previous or "").strip().rstrip("+")
    current_clean = (current or "").strip().rstrip("+")
    if not previous_clean or not current_clean:
        return False

    from packaging.version import InvalidVersion, Version

    try:
        prev_version = Version(previous_clean)
        curr_version = Version(current_clean)
    except InvalidVersion:
        return False

    return (
        prev_version.major != curr_version.major
        or prev_version.minor != curr_version.minor
    )


def _step_check_version(release, ctx, log_path: Path, *, user=None) -> None:
    from . import release as release_utils
    from packaging.version import InvalidVersion, Version

    sync_error: Optional[Exception] = None
    retry_sync = False
    try:
        _sync_with_origin_main(log_path)
    except Exception as exc:
        sync_error = exc

    if not release_utils._git_clean():
        dirty_entries = _collect_dirty_files()
        files = [entry["path"] for entry in dirty_entries]
        fixture_files = [
            f
            for f in files
            if "fixtures" in Path(f).parts and Path(f).suffix == ".json"
        ]
        changelog_dirty = "CHANGELOG.rst" in files
        version_dirty = "VERSION" in files
        allowed_dirty_files = set(fixture_files)
        if changelog_dirty:
            allowed_dirty_files.add("CHANGELOG.rst")
        if version_dirty:
            allowed_dirty_files.add("VERSION")

        if files and len(allowed_dirty_files) == len(files):
            summary = []
            for f in fixture_files:
                path = Path(f)
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    count = 0
                    models: list[str] = []
                else:
                    if isinstance(data, list):
                        count = len(data)
                        models = sorted(
                            {
                                obj.get("model", "")
                                for obj in data
                                if isinstance(obj, dict)
                            }
                        )
                    elif isinstance(data, dict):
                        count = 1
                        models = [data.get("model", "")]
                    else:  # pragma: no cover - unexpected structure
                        count = 0
                        models = []
                summary.append({"path": f, "count": count, "models": models})

            ctx["fixtures"] = summary
            commit_paths = [*fixture_files]
            if changelog_dirty:
                commit_paths.append("CHANGELOG.rst")
            if version_dirty:
                commit_paths.append("VERSION")

            log_fragments = []
            if fixture_files:
                log_fragments.append(
                    "fixtures " + ", ".join(fixture_files)
                )
            if changelog_dirty:
                log_fragments.append("CHANGELOG.rst")
            if version_dirty:
                log_fragments.append("VERSION")
            details = ", ".join(log_fragments) if log_fragments else "changes"
            _append_log(
                log_path,
                f"Committing release prep changes: {details}",
            )
            subprocess.run(["git", "add", *commit_paths], check=True)

            if changelog_dirty and version_dirty and fixture_files:
                commit_message = "chore: sync release metadata"
            elif changelog_dirty and version_dirty:
                commit_message = "chore: update version and changelog"
            elif version_dirty and fixture_files:
                commit_message = "chore: update version and fixtures"
            elif changelog_dirty and fixture_files:
                commit_message = "chore: sync release fixtures and changelog"
            elif version_dirty:
                commit_message = "chore: update version"
            elif changelog_dirty:
                commit_message = "docs: refresh changelog"
            else:
                commit_message = "chore: update fixtures"

            subprocess.run(["git", "commit", "-m", commit_message], check=True)
            _append_log(
                log_path,
                f"Release prep changes committed ({commit_message})",
            )
            ctx.pop("dirty_files", None)
            ctx.pop("dirty_commit_error", None)
            retry_sync = True
        else:
            ctx["dirty_files"] = dirty_entries
            ctx.setdefault("dirty_commit_message", DIRTY_COMMIT_DEFAULT_MESSAGE)
            ctx.pop("fixtures", None)
            ctx.pop("dirty_commit_error", None)
            if dirty_entries:
                details = ", ".join(entry["path"] for entry in dirty_entries)
            else:
                details = ""
            message = "Git repository has uncommitted changes"
            if details:
                message += f": {details}"
            if ctx.get("dirty_log_message") != message:
                _append_log(log_path, message)
                ctx["dirty_log_message"] = message
            raise DirtyRepository()
    else:
        ctx.pop("dirty_files", None)
        ctx.pop("dirty_commit_error", None)
        ctx.pop("dirty_log_message", None)

    if retry_sync and sync_error is not None:
        try:
            _sync_with_origin_main(log_path)
        except Exception as exc:
            sync_error = exc
        else:
            sync_error = None

    previous_repo_version = getattr(release, "_repo_version_before_sync", "")

    if sync_error is not None:
        raise sync_error

    dropped_versions: list[str] = ctx.setdefault("todo_purged_versions", [])
    if (
        release.version not in dropped_versions
        and _major_minor_version_changed(previous_repo_version, release.version)
    ):
        _purge_release_todos(
            log_path,
            reason=(
                f"version change from {previous_repo_version or 'unknown'} "
                f"to {release.version}"
            ),
        )
        dropped_versions.append(release.version)
        ctx.pop("todos", None)
        ctx.pop("todos_required", None)
        ctx.setdefault("todos_ack", True)

    version_path = Path("VERSION")
    if version_path.exists():
        current = version_path.read_text(encoding="utf-8").strip()
        if current:
            current_clean = current.rstrip("+") or "0.0.0"
            if Version(release.version) < Version(current_clean):
                raise Exception(
                    f"Version {release.version} is older than existing {current}"
                )

    _append_log(log_path, f"Checking if version {release.version} exists on PyPI")
    if release_utils.network_available():
        try:
            resp = requests.get(
                f"https://pypi.org/pypi/{release.package.name}/json",
                timeout=PYPI_REQUEST_TIMEOUT,
            )
            if resp.ok:
                data = resp.json()
                releases = data.get("releases", {})
                try:
                    target_version = Version(release.version)
                except InvalidVersion:
                    target_version = None

                for candidate, files in releases.items():
                    same_version = candidate == release.version
                    if target_version is not None and not same_version:
                        try:
                            same_version = Version(candidate) == target_version
                        except InvalidVersion:
                            same_version = False
                    if not same_version:
                        continue

                    has_available_files = any(
                        isinstance(file_data, dict)
                        and not file_data.get("yanked", False)
                        for file_data in files or []
                    )
                    if has_available_files:
                        raise Exception(
                            f"Version {release.version} already on PyPI"
                        )
        except Exception as exc:
            # network errors should be logged but not crash
            if "already on PyPI" in str(exc):
                raise
            _append_log(log_path, f"PyPI check failed: {exc}")
        else:
            _append_log(
                log_path,
                f"Version {release.version} not published on PyPI",
            )
    else:
        _append_log(log_path, "Network unavailable, skipping PyPI check")


def _step_handle_migrations(release, ctx, log_path: Path, *, user=None) -> None:
    _append_log(log_path, "Freeze, squash and approve migrations")
    _append_log(log_path, "Migration review acknowledged (manual step)")


def _step_changelog_docs(release, ctx, log_path: Path, *, user=None) -> None:
    _append_log(log_path, "Compose CHANGELOG and documentation")
    _append_log(log_path, "CHANGELOG and documentation review recorded")


def _step_pre_release_actions(release, ctx, log_path: Path, *, user=None) -> None:
    _append_log(log_path, "Execute pre-release actions")
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipping pre-release actions")
        return
    _sync_with_origin_main(log_path)
    try:
        subprocess.run(["scripts/generate-changelog.sh"], check=True)
    except OSError as exc:
        if _should_use_python_changelog(exc):
            _append_log(
                log_path,
                f"scripts/generate-changelog.sh failed: {exc}",
            )
            _generate_changelog_with_python(log_path)
        else:  # pragma: no cover - unexpected OSError
            raise
    else:
        _append_log(
            log_path, "Regenerated CHANGELOG.rst using scripts/generate-changelog.sh"
        )
    notes = _changelog_notes(release.version)
    staged_release_fixtures: list[Path] = []
    if notes != release.changelog:
        release.changelog = notes
        release.save(update_fields=["changelog"])
        PackageRelease.dump_fixture()
        _append_log(log_path, f"Recorded changelog notes for v{release.version}")
        release_fixture_paths = sorted(
            Path("core/fixtures").glob("releases__*.json")
        )
        if release_fixture_paths:
            subprocess.run(
                ["git", "add", *[str(path) for path in release_fixture_paths]],
                check=True,
            )
            staged_release_fixtures = release_fixture_paths
            formatted = ", ".join(_format_path(path) for path in release_fixture_paths)
            _append_log(
                log_path,
                "Staged release fixtures " + formatted,
            )
    subprocess.run(["git", "add", "CHANGELOG.rst"], check=True)
    _append_log(log_path, "Staged CHANGELOG.rst for commit")
    version_path = Path("VERSION")
    previous_version_text = ""
    if version_path.exists():
        previous_version_text = version_path.read_text(encoding="utf-8").strip()
    repo_version_before_sync = getattr(
        release, "_repo_version_before_sync", previous_version_text
    )
    version_path.write_text(f"{release.version}\n", encoding="utf-8")
    _append_log(log_path, f"Updated VERSION file to {release.version}")
    subprocess.run(["git", "add", "VERSION"], check=True)
    _append_log(log_path, "Staged VERSION for commit")
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if diff.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", f"pre-release commit {release.version}"],
            check=True,
        )
        _append_log(log_path, f"Committed VERSION update for {release.version}")
    else:
        _append_log(
            log_path, "No changes detected for VERSION or CHANGELOG; skipping commit"
        )
        subprocess.run(["git", "reset", "HEAD", "CHANGELOG.rst"], check=False)
        _append_log(log_path, "Unstaged CHANGELOG.rst")
        subprocess.run(["git", "reset", "HEAD", "VERSION"], check=False)
        _append_log(log_path, "Unstaged VERSION file")
        for path in staged_release_fixtures:
            subprocess.run(["git", "reset", "HEAD", str(path)], check=False)
            _append_log(log_path, f"Unstaged release fixture {_format_path(path)}")
    _append_log(log_path, "Pre-release actions complete")


def _step_run_tests(release, ctx, log_path: Path, *, user=None) -> None:
    _append_log(log_path, "Complete test suite with --all flag")
    _append_log(log_path, "Test suite completion acknowledged")


def _step_promote_build(release, ctx, log_path: Path, *, user=None) -> None:
    from . import release as release_utils

    _append_log(log_path, "Generating build files")
    if ctx.get("dry_run"):
        _append_log(log_path, "Dry run: skipping build promotion")
        return
    try:
        _ensure_origin_main_unchanged(log_path)
        release_utils.promote(
            package=release.to_package(),
            version=release.version,
            creds=release.to_credentials(user=user),
        )
        _append_log(
            log_path,
            f"Generated release artifacts for v{release.version}",
        )
        from glob import glob

        paths = ["VERSION", *glob("core/fixtures/releases__*.json")]
        diff = subprocess.run(
            ["git", "status", "--porcelain", *paths],
            capture_output=True,
            text=True,
        )
        if diff.stdout.strip():
            subprocess.run(["git", "add", *paths], check=True)
            _append_log(log_path, "Staged release metadata updates")
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    f"chore: update release metadata for v{release.version}",
                ],
                check=True,
            )
            _append_log(
                log_path,
                f"Committed release metadata for v{release.version}",
            )
        _push_release_changes(log_path)
        PackageRelease.dump_fixture()
        _append_log(log_path, "Updated release fixtures")
    except Exception:
        _clean_repo()
        raise
    target_name = _release_log_name(release.package.name, release.version)
    new_log = log_path.with_name(target_name)
    if log_path != new_log:
        if new_log.exists():
            new_log.unlink()
        log_path.rename(new_log)
    else:
        new_log = log_path
    ctx["log"] = new_log.name
    _append_log(new_log, "Build complete")


def _step_release_manager_approval(
    release, ctx, log_path: Path, *, user=None
) -> None:
    if release.to_credentials(user=user) is None:
        ctx.pop("release_approval", None)
        if not ctx.get("approval_credentials_missing"):
            _append_log(log_path, "Release manager publishing credentials missing")
        ctx["approval_credentials_missing"] = True
        ctx["awaiting_approval"] = True
        raise ApprovalRequired()

    missing_before = ctx.pop("approval_credentials_missing", None)
    if missing_before:
        ctx.pop("awaiting_approval", None)
    decision = ctx.get("release_approval")
    if decision == "approved":
        ctx.pop("release_approval", None)
        ctx.pop("awaiting_approval", None)
        ctx.pop("approval_credentials_missing", None)
        _append_log(log_path, "Release manager approved release")
        return
    if decision == "rejected":
        ctx.pop("release_approval", None)
        ctx.pop("awaiting_approval", None)
        ctx.pop("approval_credentials_missing", None)
        _append_log(log_path, "Release manager rejected release")
        raise RuntimeError(
            _("Release manager rejected the release. Restart required."),
        )
    if not ctx.get("awaiting_approval"):
        ctx["awaiting_approval"] = True
        _append_log(log_path, "Awaiting release manager approval")
    else:
        ctx["awaiting_approval"] = True
    raise ApprovalRequired()


def _step_publish(release, ctx, log_path: Path, *, user=None) -> None:
    from . import release as release_utils

    if ctx.get("dry_run"):
        test_repository_url = os.environ.get(
            "PYPI_TEST_REPOSITORY_URL", "https://test.pypi.org/legacy/"
        )
        test_creds = release.to_credentials(user=user)
        if not (test_creds and test_creds.has_auth()):
            test_creds = release_utils.Credentials(
                token=os.environ.get("PYPI_TEST_API_TOKEN"),
                username=os.environ.get("PYPI_TEST_USERNAME"),
                password=os.environ.get("PYPI_TEST_PASSWORD"),
            )
            if not test_creds.has_auth():
                test_creds = None
        target = release_utils.RepositoryTarget(
            name="Test PyPI",
            repository_url=(test_repository_url or None),
            credentials=test_creds,
            verify_availability=False,
        )
        label = target.repository_url or target.name
        dist_path = Path("dist")
        if not dist_path.exists():
            _append_log(log_path, "Dry run: building distribution artifacts")
            package = release.to_package()
            version_path = (
                Path(package.version_path)
                if package.version_path
                else Path("VERSION")
            )
            original_version = (
                version_path.read_text(encoding="utf-8")
                if version_path.exists()
                else None
            )
            pyproject_path = Path("pyproject.toml")
            original_pyproject = (
                pyproject_path.read_text(encoding="utf-8")
                if pyproject_path.exists()
                else None
            )
            try:
                release_utils.build(
                    package=package,
                    version=release.version,
                    creds=release.to_credentials(user=user),
                    dist=True,
                    tests=False,
                    twine=False,
                    git=False,
                    tag=False,
                    stash=True,
                )
            except release_utils.ReleaseError as exc:
                _append_log(
                    log_path,
                    f"Dry run: failed to prepare distribution artifacts ({exc})",
                )
                raise
            finally:
                if original_version is None:
                    if version_path.exists():
                        version_path.unlink()
                else:
                    version_path.write_text(original_version, encoding="utf-8")
                if original_pyproject is None:
                    if pyproject_path.exists():
                        pyproject_path.unlink()
                else:
                    pyproject_path.write_text(original_pyproject, encoding="utf-8")
        _append_log(log_path, f"Dry run: uploading distribution to {label}")
        release_utils.publish(
            package=release.to_package(),
            version=release.version,
            creds=target.credentials or release.to_credentials(user=user),
            repositories=[target],
        )
        _append_log(log_path, "Dry run: skipped release metadata updates")
        return

    targets = release.build_publish_targets(user=user)
    repo_labels = []
    for target in targets:
        label = target.name
        if target.repository_url:
            label = f"{label} ({target.repository_url})"
        repo_labels.append(label)
    if repo_labels:
        _append_log(
            log_path,
            "Uploading distribution" if len(repo_labels) == 1 else "Uploading distribution to: " + ", ".join(repo_labels),
        )
    else:
        _append_log(log_path, "Uploading distribution")
    publish_warning: release_utils.PostPublishWarning | None = None
    try:
        release_utils.publish(
            package=release.to_package(),
            version=release.version,
            creds=release.to_credentials(user=user),
            repositories=targets,
        )
    except release_utils.PostPublishWarning as warning:
        publish_warning = warning

    if publish_warning is not None:
        message = str(publish_warning)
        followups = _dedupe_preserve_order(publish_warning.followups)
        warning_entries = ctx.setdefault("warnings", [])
        if not any(entry.get("message") == message for entry in warning_entries):
            entry: dict[str, object] = {"message": message}
            if followups:
                entry["followups"] = followups
            warning_entries.append(entry)
        _append_log(log_path, message)
        for note in followups:
            _append_log(log_path, f"Follow-up: {note}")
    release.pypi_url = (
        f"https://pypi.org/project/{release.package.name}/{release.version}/"
    )
    github_url = ""
    for target in targets[1:]:
        if target.repository_url and "github.com" in target.repository_url:
            github_url = release.github_package_url() or ""
            break
    if github_url:
        release.github_url = github_url
    else:
        release.github_url = ""
    release.release_on = timezone.now()
    release.save(update_fields=["pypi_url", "github_url", "release_on"])
    PackageRelease.dump_fixture()
    _append_log(log_path, f"Recorded PyPI URL: {release.pypi_url}")
    if release.github_url:
        _append_log(log_path, f"Recorded GitHub URL: {release.github_url}")
    fixture_paths = [
        str(path) for path in Path("core/fixtures").glob("releases__*.json")
    ]
    if fixture_paths:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", *fixture_paths],
            capture_output=True,
            text=True,
            check=True,
        )
        if status.stdout.strip():
            subprocess.run(["git", "add", *fixture_paths], check=True)
            _append_log(log_path, "Staged publish metadata updates")
            commit_message = f"chore: record publish metadata for v{release.version}"
            subprocess.run(["git", "commit", "-m", commit_message], check=True)
            _append_log(
                log_path, f"Committed publish metadata for v{release.version}"
            )
            _push_release_changes(log_path)
        else:
            _append_log(
                log_path,
                "No release metadata updates detected after publish; skipping commit",
            )
    _append_log(log_path, "Upload complete")


FIXTURE_REVIEW_STEP_NAME = "Freeze, squash and approve migrations"


PUBLISH_STEPS = [
    ("Check version number availability", _step_check_version),
    ("Confirm release TODO completion", _step_check_todos),
    (FIXTURE_REVIEW_STEP_NAME, _step_handle_migrations),
    ("Compose CHANGELOG and documentation", _step_changelog_docs),
    ("Execute pre-release actions", _step_pre_release_actions),
    ("Build release artifacts", _step_promote_build),
    ("Complete test suite with --all flag", _step_run_tests),
    ("Get Release Manager Approval", _step_release_manager_approval),
    ("Upload final build to PyPI", _step_publish),
]


@csrf_exempt
def rfid_login(request):
    """Authenticate a user using an RFID."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)

    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        data = request.POST

    rfid = data.get("rfid")
    if not rfid:
        return JsonResponse({"detail": "rfid required"}, status=400)

    redirect_to = data.get(REDIRECT_FIELD_NAME) or data.get("next")
    if redirect_to and not url_has_allowed_host_and_scheme(
        redirect_to,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_to = ""

    user = authenticate(request, rfid=rfid)
    if user is None:
        return JsonResponse({"detail": "invalid RFID"}, status=401)

    login(request, user)
    if redirect_to:
        target = redirect_to
    elif user.is_staff:
        target = reverse("admin:index")
    else:
        target = "/"
    return JsonResponse(
        {"id": user.id, "username": user.username, "redirect": target}
    )


@api_login_required
def product_list(request):
    """Return a JSON list of products."""

    products = list(
        Product.objects.values("id", "name", "description", "renewal_period")
    )
    return JsonResponse({"products": products})


@csrf_exempt
@api_login_required
def add_live_subscription(request):
    """Create a live subscription for a customer account from POSTed JSON."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)

    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        data = request.POST

    account_id = data.get("account_id")
    product_id = data.get("product_id")

    if not account_id or not product_id:
        return JsonResponse(
            {"detail": "account_id and product_id required"}, status=400
        )

    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return JsonResponse({"detail": "invalid product"}, status=404)

    try:
        account = CustomerAccount.objects.get(id=account_id)
    except CustomerAccount.DoesNotExist:
        return JsonResponse({"detail": "invalid account"}, status=404)

    start_date = timezone.now().date()
    account.live_subscription_product = product
    account.live_subscription_start_date = start_date
    account.live_subscription_next_renewal = start_date + timedelta(
        days=product.renewal_period
    )
    account.save()

    return JsonResponse({"id": account.id})


@api_login_required
def live_subscription_list(request):
    """Return live subscriptions for the given account_id."""

    account_id = request.GET.get("account_id")
    if not account_id:
        return JsonResponse({"detail": "account_id required"}, status=400)

    try:
        account = CustomerAccount.objects.select_related("live_subscription_product").get(
            id=account_id
        )
    except CustomerAccount.DoesNotExist:
        return JsonResponse({"detail": "invalid account"}, status=404)

    subs = []
    product = account.live_subscription_product
    if product:
        next_renewal = account.live_subscription_next_renewal
        if not next_renewal and account.live_subscription_start_date:
            next_renewal = account.live_subscription_start_date + timedelta(
                days=product.renewal_period
            )

        subs.append(
            {
                "id": account.id,
                "product__name": product.name,
                "next_renewal": next_renewal,
            }
        )

    return JsonResponse({"live_subscriptions": subs})


@csrf_exempt
@api_login_required
def rfid_batch(request):
    """Export or import RFID tags in batch."""

    if request.method == "GET":
        color = request.GET.get("color", RFID.BLACK).upper()
        released = request.GET.get("released")
        if released is not None:
            released = released.lower()
        qs = RFID.objects.all()
        if color != "ALL":
            qs = qs.filter(color=color)
        if released in ("true", "false"):
            qs = qs.filter(released=(released == "true"))
        tags = []
        for t in qs.order_by("rfid"):
            ids = list(t.energy_accounts.values_list("id", flat=True))
            names = list(
                t.energy_accounts.exclude(name="").values_list("name", flat=True)
            )
            payload = {
                "rfid": t.rfid,
                "custom_label": t.custom_label,
                "customer_accounts": ids,
                "customer_account_names": names,
                "external_command": t.external_command,
                "post_auth_command": t.post_auth_command,
                "allowed": t.allowed,
                "color": t.color,
                "released": t.released,
            }
            payload["energy_accounts"] = ids
            payload["energy_account_names"] = names
            tags.append(payload)
        return JsonResponse({"rfids": tags})

    if request.method == "POST":
        try:
            data = json.loads(request.body.decode())
        except json.JSONDecodeError:
            return JsonResponse({"detail": "invalid JSON"}, status=400)

        tags = data.get("rfids") if isinstance(data, dict) else data
        if not isinstance(tags, list):
            return JsonResponse({"detail": "rfids list required"}, status=400)

        count = 0
        for row in tags:
            rfid = (row.get("rfid") or "").strip()
            if not rfid:
                continue
            allowed = row.get("allowed", True)
            energy_accounts = (
                row.get("customer_accounts")
                or row.get("energy_accounts")
                or []
            )
            account_names = row.get("customer_account_names") or row.get(
                "energy_account_names"
            )
            color = (row.get("color") or RFID.BLACK).strip().upper() or RFID.BLACK
            released = row.get("released", False)
            if isinstance(released, str):
                released = released.lower() == "true"
            custom_label = (row.get("custom_label") or "").strip()
            external_command = row.get("external_command")
            if not isinstance(external_command, str):
                external_command = ""
            else:
                external_command = external_command.strip()
            post_auth_command = row.get("post_auth_command")
            if not isinstance(post_auth_command, str):
                post_auth_command = ""
            else:
                post_auth_command = post_auth_command.strip()

            tag, _ = RFID.update_or_create_from_code(
                rfid,
                {
                    "allowed": allowed,
                    "color": color,
                    "released": released,
                    "custom_label": custom_label,
                    "external_command": external_command,
                    "post_auth_command": post_auth_command,
                },
            )
            accounts_qs = CustomerAccount.objects.none()
            if energy_accounts:
                accounts_qs = CustomerAccount.objects.filter(id__in=energy_accounts)
            elif account_names:
                names = [
                    value.strip()
                    for value in str(account_names).split(",")
                    if value.strip()
                ]
                accounts_qs = CustomerAccount.objects.filter(name__in=names)
            if accounts_qs:
                tag.energy_accounts.set(accounts_qs)
            else:
                tag.energy_accounts.clear()
            count += 1

        return JsonResponse({"imported": count})

    return JsonResponse({"detail": "GET or POST required"}, status=400)


@staff_member_required
def release_progress(request, pk: int, action: str):
    try:
        release = PackageRelease.objects.get(pk=pk)
    except PackageRelease.DoesNotExist:
        return _render_release_progress_error(
            request,
            None,
            action,
            _("The requested release could not be found."),
            status=404,
            debug_info={"pk": pk, "action": action},
        )

    if action != "publish":
        return _render_release_progress_error(
            request,
            release,
            action,
            _("Unknown release action."),
            status=404,
            debug_info={"action": action},
        )
    session_key = f"release_publish_{pk}"
    lock_path = Path("locks") / f"release_publish_{pk}.json"
    restart_path = Path("locks") / f"release_publish_{pk}.restarts"
    log_dir, log_dir_warning = _resolve_release_log_dir(Path(settings.LOG_DIR))
    log_dir_warning_message = log_dir_warning

    version_path = Path("VERSION")
    repo_version_before_sync = ""
    if version_path.exists():
        repo_version_before_sync = version_path.read_text(encoding="utf-8").strip()
    setattr(release, "_repo_version_before_sync", repo_version_before_sync)

    if not release.is_current:
        if release.is_published:
            return _render_release_progress_error(
                request,
                release,
                action,
                _("This release was already published and no longer matches the repository version."),
                status=409,
                debug_info={
                    "release_version": release.version,
                    "repository_version": repo_version_before_sync,
                    "pypi_url": release.pypi_url,
                },
            )
        updated, previous_version = _sync_release_with_revision(release)
        if updated:
            request.session.pop(session_key, None)
            if lock_path.exists():
                lock_path.unlink()
            if restart_path.exists():
                restart_path.unlink()
            pattern = f"pr.{release.package.name}.v{previous_version}*.log"
            for log_file in log_dir.glob(pattern):
                log_file.unlink()
        if not release.is_current:
            return _render_release_progress_error(
                request,
                release,
                action,
                _("The repository VERSION file does not match this release."),
                status=409,
                debug_info={
                    "release_version": release.version,
                    "repository_version": repo_version_before_sync,
                },
            )

    if request.GET.get("restart"):
        count = 0
        if restart_path.exists():
            try:
                count = int(restart_path.read_text(encoding="utf-8"))
            except Exception:
                count = 0
        restart_path.parent.mkdir(parents=True, exist_ok=True)
        restart_path.write_text(str(count + 1), encoding="utf-8")
        _clean_repo()
        release.pypi_url = ""
        release.release_on = None
        release.save(update_fields=["pypi_url", "release_on"])
        request.session.pop(session_key, None)
        if lock_path.exists():
            lock_path.unlink()
        pattern = f"pr.{release.package.name}.v{release.version}*.log"
        for f in log_dir.glob(pattern):
            f.unlink()
        return redirect(request.path)
    ctx = request.session.get(session_key)
    if ctx is None and lock_path.exists():
        try:
            ctx = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            ctx = {"step": 0}
    if ctx is None:
        ctx = {"step": 0}
        if restart_path.exists():
            restart_path.unlink()
    if log_dir_warning_message:
        ctx["log_dir_warning_message"] = log_dir_warning_message
    else:
        log_dir_warning_message = ctx.get("log_dir_warning_message")

    if "changelog_report_url" not in ctx:
        try:
            ctx["changelog_report_url"] = reverse("admin:system-changelog-report")
        except NoReverseMatch:
            ctx["changelog_report_url"] = ""

    steps = PUBLISH_STEPS
    total_steps = len(steps)
    step_count = ctx.get("step", 0)
    started_flag = bool(ctx.get("started"))
    paused_flag = bool(ctx.get("paused"))
    error_flag = bool(ctx.get("error"))
    done_flag = step_count >= total_steps and not error_flag
    start_enabled = (not started_flag or paused_flag) and not done_flag and not error_flag

    ctx["dry_run"] = bool(ctx.get("dry_run"))

    if request.GET.get("set_dry_run") is not None:
        if start_enabled:
            ctx["dry_run"] = bool(request.GET.get("dry_run"))
            request.session[session_key] = ctx
        return redirect(request.path)

    manager = release.release_manager or release.package.release_manager
    credentials_ready = bool(release.to_credentials(user=request.user))
    if credentials_ready and ctx.get("approval_credentials_missing"):
        ctx.pop("approval_credentials_missing", None)

    ack_todos_requested = bool(request.GET.get("ack_todos"))

    if request.GET.get("start"):
        if start_enabled:
            ctx["dry_run"] = bool(request.GET.get("dry_run"))
        ctx["started"] = True
        ctx["paused"] = False
    if (
        ctx.get("awaiting_approval")
        and not ctx.get("approval_credentials_missing")
        and credentials_ready
    ):
        if request.GET.get("approve"):
            ctx["release_approval"] = "approved"
        if request.GET.get("reject"):
            ctx["release_approval"] = "rejected"
    resume_requested = bool(request.GET.get("resume"))

    if request.GET.get("pause") and ctx.get("started"):
        ctx["paused"] = True

    if resume_requested:
        if not ctx.get("started"):
            ctx["started"] = True
        if ctx.get("paused"):
            ctx["paused"] = False
    restart_count = 0
    if restart_path.exists():
        try:
            restart_count = int(restart_path.read_text(encoding="utf-8"))
        except Exception:
            restart_count = 0
    step_count = ctx.get("step", 0)
    step_param = request.GET.get("step")
    if resume_requested and step_param is None:
        step_param = str(step_count)

    pending_items = Todo.refresh_active()
    blocking_todos = [
        todo for todo in pending_items if _todo_blocks_publish(todo, release)
    ]
    if not blocking_todos:
        ctx["todos_ack"] = True
        ctx["todos_ack_auto"] = True
    elif ack_todos_requested:
        failures = []
        for todo in blocking_todos:
            result = todo.check_on_done_condition()
            if not result.passed:
                failures.append((todo, result))
        if failures:
            ctx["todos_ack"] = False
            ctx.pop("todos_ack_auto", None)
            for todo, result in failures:
                messages.error(request, _format_condition_failure(todo, result))
        else:
            ctx["todos_ack"] = True
            ctx.pop("todos_ack_auto", None)
    else:
        if ctx.pop("todos_ack_auto", None):
            ctx["todos_ack"] = False
        else:
            ctx.setdefault("todos_ack", False)

    if ctx.get("todos_ack"):
        ctx.pop("todos_block_logged", None)
        ctx.pop("todos", None)
        ctx.pop("todos_required", None)
    else:
        ctx["todos"] = [
            {
                "id": todo.pk,
                "request": todo.request,
                "url": todo.url,
                "request_details": todo.request_details,
            }
            for todo in blocking_todos
        ]
        ctx["todos_required"] = True

    log_name = _release_log_name(release.package.name, release.version)
    if ctx.get("log") != log_name:
        ctx = {
            "step": 0,
            "log": log_name,
            "started": ctx.get("started", False),
        }
        step_count = 0
        if not blocking_todos:
            ctx["todos_ack"] = True
    log_path = log_dir / log_name
    ctx.setdefault("log", log_name)
    ctx.setdefault("paused", False)
    ctx.setdefault("dirty_commit_message", DIRTY_COMMIT_DEFAULT_MESSAGE)

    dirty_action = request.GET.get("dirty_action")
    if dirty_action and ctx.get("dirty_files"):
        if dirty_action == "discard":
            _clean_repo()
            remaining = _collect_dirty_files()
            if remaining:
                ctx["dirty_files"] = remaining
                ctx.pop("dirty_commit_error", None)
            else:
                ctx.pop("dirty_files", None)
                ctx.pop("dirty_commit_error", None)
                ctx.pop("dirty_log_message", None)
                _append_log(log_path, "Discarded local changes before publish")
        elif dirty_action == "commit":
            message = request.GET.get("dirty_message", "").strip()
            if not message:
                message = ctx.get("dirty_commit_message") or DIRTY_COMMIT_DEFAULT_MESSAGE
            ctx["dirty_commit_message"] = message
            try:
                subprocess.run(["git", "add", "--all"], check=True)
                subprocess.run(["git", "commit", "-m", message], check=True)
            except subprocess.CalledProcessError as exc:
                ctx["dirty_commit_error"] = _format_subprocess_error(exc)
            else:
                ctx.pop("dirty_commit_error", None)
                remaining = _collect_dirty_files()
                if remaining:
                    ctx["dirty_files"] = remaining
                else:
                    ctx.pop("dirty_files", None)
                    ctx.pop("dirty_log_message", None)
                _append_log(
                    log_path,
                    _("Committed pending changes: %(message)s")
                    % {"message": message},
                )

    if (
        ctx.get("started")
        and step_count == 0
        and (step_param is None or step_param == "0")
    ):
        if log_path.exists():
            log_path.unlink()
        ctx.pop("log_dir_warning_logged", None)

    if log_dir_warning_message and not ctx.get("log_dir_warning_logged"):
        _append_log(log_path, log_dir_warning_message)
        ctx["log_dir_warning_logged"] = True

    fixtures_step_index = next(
        (
            index
            for index, (name, _) in enumerate(steps)
            if name == FIXTURE_REVIEW_STEP_NAME
        ),
        None,
    )
    error = ctx.get("error")

    if (
        ctx.get("started")
        and not ctx.get("paused")
        and step_param is not None
        and not error
        and step_count < len(steps)
    ):
        to_run = int(step_param)
        if to_run == step_count:
            name, func = steps[to_run]
            try:
                func(release, ctx, log_path, user=request.user)
            except PendingTodos:
                pass
            except ApprovalRequired:
                pass
            except DirtyRepository:
                pass
            except Exception as exc:  # pragma: no cover - best effort logging
                _append_log(log_path, f"{name} failed: {exc}")
                ctx["error"] = str(exc)
                request.session[session_key] = ctx
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(json.dumps(ctx), encoding="utf-8")
            else:
                step_count += 1
                ctx["step"] = step_count
                request.session[session_key] = ctx
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(json.dumps(ctx), encoding="utf-8")

    done = step_count >= len(steps) and not ctx.get("error")

    show_log = ctx.get("started") or step_count > 0 or done or ctx.get("error")
    if show_log and log_path.exists():
        log_content = log_path.read_text(encoding="utf-8")
    else:
        log_content = ""
    next_step = (
        step_count
        if ctx.get("started")
        and not ctx.get("paused")
        and not done
        and not ctx.get("error")
        else None
    )
    has_pending_todos = bool(
        ctx.get("todos_required") and not ctx.get("todos_ack")
    )
    if has_pending_todos:
        next_step = None
    dirty_files = ctx.get("dirty_files")
    if dirty_files:
        next_step = None
    awaiting_approval = bool(ctx.get("awaiting_approval"))
    approval_credentials_missing = bool(ctx.get("approval_credentials_missing"))
    if awaiting_approval:
        next_step = None
    if approval_credentials_missing:
        next_step = None
    paused = ctx.get("paused", False)

    step_names = [s[0] for s in steps]
    approval_credentials_ready = credentials_ready
    credentials_blocking = approval_credentials_missing or (
        awaiting_approval and not approval_credentials_ready
    )
    step_states = []
    for index, name in enumerate(step_names):
        if index < step_count:
            status = "complete"
            icon = "✅"
            label = _("Completed")
        elif error and index == step_count:
            status = "error"
            icon = "❌"
            label = _("Failed")
        elif paused and ctx.get("started") and index == step_count and not done:
            status = "paused"
            icon = "⏸️"
            label = _("Paused")
        elif (
            has_pending_todos
            and ctx.get("started")
            and index == step_count
            and not done
        ):
            status = "blocked"
            icon = "📝"
            label = _("Awaiting checklist")
        elif (
            credentials_blocking
            and ctx.get("started")
            and index == step_count
            and not done
        ):
            status = "missing-credentials"
            icon = "🔐"
            label = _("Credentials required")
        elif (
            awaiting_approval
            and approval_credentials_ready
            and ctx.get("started")
            and index == step_count
            and not done
        ):
            status = "awaiting-approval"
            icon = "🤝"
            label = _("Awaiting approval")
        elif ctx.get("started") and index == step_count and not done:
            status = "active"
            icon = "⏳"
            label = _("In progress")
        else:
            status = "pending"
            icon = "⬜"
            label = _("Pending")
        step_states.append(
            {
                "index": index + 1,
                "name": name,
                "status": status,
                "icon": icon,
                "label": label,
            }
        )

    is_running = ctx.get("started") and not paused and not done and not ctx.get("error")
    resume_available = (
        ctx.get("started")
        and not paused
        and not done
        and not ctx.get("error")
        and step_count < len(steps)
        and next_step is None
    )
    can_resume = ctx.get("started") and paused and not done and not ctx.get("error")
    release_manager_owner = manager.owner_display() if manager else ""
    try:
        current_user_admin_url = reverse(
            "admin:teams_user_change", args=[request.user.pk]
        )
    except NoReverseMatch:
        current_user_admin_url = reverse(
            "admin:core_user_change", args=[request.user.pk]
        )

    fixtures_summary = ctx.get("fixtures")
    if (
        fixtures_summary
        and fixtures_step_index is not None
        and step_count > fixtures_step_index
    ):
        fixtures_summary = None

    todos_display = ctx.get("todos") if has_pending_todos else None

    dry_run_active = bool(ctx.get("dry_run"))
    dry_run_toggle_enabled = not is_running and not done and not ctx.get("error")

    context = {
        "release": release,
        "action": "publish",
        "steps": step_names,
        "current_step": step_count,
        "next_step": next_step,
        "done": done,
        "error": ctx.get("error"),
        "log_content": log_content,
        "log_path": str(log_path),
        "cert_log": ctx.get("cert_log"),
        "fixtures": fixtures_summary,
        "todos": todos_display,
        "changelog_report_url": ctx.get("changelog_report_url", ""),
        "dirty_files": dirty_files,
        "dirty_commit_message": ctx.get("dirty_commit_message", DIRTY_COMMIT_DEFAULT_MESSAGE),
        "dirty_commit_error": ctx.get("dirty_commit_error"),
        "restart_count": restart_count,
        "started": ctx.get("started", False),
        "paused": paused,
        "show_log": show_log,
        "step_states": step_states,
        "has_pending_todos": has_pending_todos,
        "awaiting_approval": awaiting_approval,
        "approval_credentials_missing": approval_credentials_missing,
        "approval_credentials_ready": approval_credentials_ready,
        "release_manager_owner": release_manager_owner,
        "has_release_manager": bool(manager),
        "current_user_admin_url": current_user_admin_url,
        "is_running": is_running,
        "resume_available": resume_available,
        "can_resume": can_resume,
        "dry_run": dry_run_active,
        "dry_run_toggle_enabled": dry_run_toggle_enabled,
        "warnings": ctx.get("warnings", []),
    }
    request.session[session_key] = ctx
    if done or ctx.get("error"):
        if lock_path.exists():
            lock_path.unlink()
    else:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(ctx), encoding="utf-8")
    template = get_template("core/release_progress.html")
    content = template.render(context, request)
    signals.template_rendered.send(
        sender=template.__class__,
        template=template,
        context=context,
        using=getattr(getattr(template, "engine", None), "name", None),
    )
    response = HttpResponse(content)
    response.context = context
    response.templates = [template]
    return response


def _dedupe_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _parse_todo_auth_directives(query: str):
    directives = {
        "require_logout": False,
        "users": [],
        "permissions": [],
        "notes": [],
    }
    if not query:
        return "", directives

    remaining = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key != "_todo_auth":
            remaining.append((key, value))
            continue
        token = (value or "").strip()
        if not token:
            continue
        kind, _, payload = token.partition(":")
        kind = kind.strip().lower()
        payload = payload.strip()
        if kind in {"logout", "anonymous", "anon"}:
            directives["require_logout"] = True
        elif kind in {"user", "username"} and payload:
            directives["users"].append(payload)
        elif kind in {"perm", "permission"} and payload:
            directives["permissions"].append(payload)
        else:
            directives["notes"].append(token)

    sanitized_query = urlencode(remaining, doseq=True)
    return sanitized_query, directives


def _todo_iframe_url(request, todo: Todo):
    """Return a safe iframe URL and auth context for ``todo``."""

    fallback = reverse("admin:core_todo_change", args=[todo.pk])
    raw_url = (todo.url or "").strip()

    auth_context = {
        "require_logout": False,
        "users": [],
        "permissions": [],
        "notes": [],
    }

    def _final_context(target_url: str):
        return {
            "target_url": target_url or fallback,
            "require_logout": auth_context["require_logout"],
            "users": _dedupe_preserve_order(auth_context["users"]),
            "permissions": _dedupe_preserve_order(auth_context["permissions"]),
            "notes": _dedupe_preserve_order(auth_context["notes"]),
            "has_requirements": bool(
                auth_context["require_logout"]
                or auth_context["users"]
                or auth_context["permissions"]
                or auth_context["notes"]
            ),
        }

    if not raw_url:
        return fallback, _final_context(fallback)

    focus_path = reverse("todo-focus", args=[todo.pk])
    focus_norm = focus_path.strip("/").lower()

    def _is_focus_target(target: str) -> bool:
        if not target:
            return False
        parsed_target = urlsplit(target)
        path = parsed_target.path
        if not path and not parsed_target.scheme and not parsed_target.netloc:
            path = target.split("?", 1)[0].split("#", 1)[0]
        normalized = path.strip("/").lower()
        return normalized == focus_norm if normalized else False

    if _is_focus_target(raw_url):
        return fallback, _final_context(fallback)

    parsed = urlsplit(raw_url)

    def _merge_directives(parsed_result):
        sanitized_query, directives = _parse_todo_auth_directives(parsed_result.query)
        if directives["require_logout"]:
            auth_context["require_logout"] = True
        auth_context["users"].extend(directives["users"])
        auth_context["permissions"].extend(directives["permissions"])
        auth_context["notes"].extend(directives["notes"])
        return parsed_result._replace(query=sanitized_query)

    if not parsed.scheme and not parsed.netloc:
        sanitized = _merge_directives(parsed)
        path = sanitized.path or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        relative_url = urlunsplit(("", "", path, sanitized.query, sanitized.fragment))
        if _is_focus_target(relative_url):
            return fallback, _final_context(fallback)
        return relative_url or fallback, _final_context(relative_url)

    if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
        return fallback, _final_context(fallback)

    request_host = request.get_host().strip().lower()
    host_without_port = request_host.split(":", 1)[0]
    allowed_hosts = {
        request_host,
        host_without_port,
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
    }

    site_domain = ""
    try:
        site_domain = Site.objects.get_current().domain.strip().lower()
    except Site.DoesNotExist:
        site_domain = ""
    if site_domain:
        allowed_hosts.add(site_domain)
        allowed_hosts.add(site_domain.split(":", 1)[0])

    for host in getattr(settings, "ALLOWED_HOSTS", []):
        if not isinstance(host, str):
            continue
        normalized = host.strip().lower()
        if not normalized or normalized.startswith("*"):
            continue
        allowed_hosts.add(normalized)
        allowed_hosts.add(normalized.split(":", 1)[0])

    hostname = (parsed.hostname or "").strip().lower()
    netloc = parsed.netloc.strip().lower()
    if hostname in allowed_hosts or netloc in allowed_hosts:
        sanitized = _merge_directives(parsed)
        path = sanitized.path or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        relative_url = urlunsplit(("", "", path, sanitized.query, sanitized.fragment))
        if _is_focus_target(relative_url):
            return fallback, _final_context(fallback)
        return relative_url or fallback, _final_context(relative_url)

    return fallback, _final_context(fallback)


@staff_member_required
def todo_focus(request, pk: int):
    todo = get_object_or_404(Todo, pk=pk, is_deleted=False)
    if todo.done_on:
        return redirect(_get_return_url(request))

    iframe_url, focus_auth = _todo_iframe_url(request, todo)
    focus_target_url = focus_auth.get("target_url", iframe_url) if focus_auth else iframe_url
    context = {
        "todo": todo,
        "iframe_url": iframe_url,
        "focus_target_url": focus_target_url,
        "focus_auth": focus_auth,
        "next_url": _get_return_url(request),
        "done_url": reverse("todo-done", args=[todo.pk]),
        "delete_url": reverse("todo-delete", args=[todo.pk]),
        "snapshot_url": reverse("todo-snapshot", args=[todo.pk]),
    }
    return render(request, "core/todo_focus.html", context)


@staff_member_required
@require_POST
def todo_done(request, pk: int):
    redirect_to = _get_return_url(request)
    try:
        todo = Todo.objects.get(pk=pk, is_deleted=False, done_on__isnull=True)
    except Todo.DoesNotExist:
        return redirect(redirect_to)
    result = todo.check_on_done_condition()
    if not result.passed:
        messages.error(request, _format_condition_failure(todo, result))
        return redirect(redirect_to)
    todo.done_on = timezone.now()
    todo.populate_done_metadata(request.user)
    todo.save(
        update_fields=[
            "done_on",
            "done_node",
            "done_version",
            "done_revision",
            "done_username",
        ]
    )
    return redirect(redirect_to)


@staff_member_required
@require_POST
def todo_delete(request, pk: int):
    redirect_to = reverse("admin:index")
    try:
        todo = Todo.objects.get(pk=pk, is_deleted=False)
    except Todo.DoesNotExist:
        return redirect(redirect_to)
    todo.is_deleted = True
    todo.save(update_fields=["is_deleted"])
    return redirect(redirect_to)


@staff_member_required
@require_POST
def todo_snapshot(request, pk: int):
    todo = get_object_or_404(Todo, pk=pk, is_deleted=False)
    if todo.done_on:
        return JsonResponse({"detail": _("This TODO has already been completed.")}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": _("Invalid JSON payload.")}, status=400)

    image_data = payload.get("image", "") if isinstance(payload, dict) else ""
    if not isinstance(image_data, str) or not image_data.startswith("data:image/png;base64,"):
        return JsonResponse({"detail": _("A PNG data URL is required.")}, status=400)

    try:
        encoded = image_data.split(",", 1)[1]
    except IndexError:
        return JsonResponse({"detail": _("Screenshot data is incomplete.")}, status=400)

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return JsonResponse({"detail": _("Unable to decode screenshot data.")}, status=400)

    if not image_bytes:
        return JsonResponse({"detail": _("Screenshot data is empty.")}, status=400)

    max_size = 5 * 1024 * 1024
    if len(image_bytes) > max_size:
        return JsonResponse({"detail": _("Screenshot is too large to store.")}, status=400)

    relative_path = Path("screenshots") / f"todo-{todo.pk}-{uuid.uuid4().hex}.png"
    full_path = settings.LOG_DIR / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with full_path.open("wb") as fh:
        fh.write(image_bytes)

    primary_text = strip_tags(todo.request or "").strip()
    details_text = strip_tags(todo.request_details or "").strip()
    alt_parts = [part for part in (primary_text, details_text) if part]
    if alt_parts:
        alt_text = " — ".join(alt_parts)
    else:
        alt_text = _("TODO %(id)s snapshot") % {"id": todo.pk}

    sample = save_screenshot(
        relative_path,
        method="TODO_QA",
        content=alt_text,
        user=request.user if request.user.is_authenticated else None,
    )

    if sample is None:
        try:
            full_path.unlink()
        except FileNotFoundError:
            pass
        return JsonResponse({"detail": _("Duplicate snapshot ignored.")})

    return JsonResponse({"detail": _("Snapshot saved."), "sample": str(sample.pk)})
