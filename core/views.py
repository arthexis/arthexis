import json
import logging
import shutil
from datetime import timedelta

import requests
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.contrib.sites.models import Site
from django.http import Http404, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.urls import NoReverseMatch, reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.utils.http import url_has_allowed_host_and_scheme
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import errno
import subprocess

from django.template.loader import get_template
from django.test import signals

from utils import revision
from utils.api import api_login_required

logger = logging.getLogger(__name__)

from . import changelog as changelog_utils
from .models import Product, EnergyAccount, PackageRelease, Todo
from .models import RFID, WorldSimulator


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
            [[]],
            {"fields": ["name"], "limit": 50},
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


TODO_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def _release_log_name(package_name: str, version: str) -> str:
    return f"pr.{package_name}.v{version}.log"


def _clean_repo() -> None:
    """Return the git repository to a clean state."""
    subprocess.run(["git", "reset", "--hard"], check=False)
    subprocess.run(["git", "clean", "-fd"], check=False)


def _format_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _next_patch_version(version: str) -> str:
    from packaging.version import InvalidVersion, Version

    try:
        parsed = Version(version)
    except InvalidVersion:
        parts = version.split(".")
        for index in range(len(parts) - 1, -1, -1):
            segment = parts[index]
            if segment.isdigit():
                parts[index] = str(int(segment) + 1)
                return ".".join(parts)
        return version
    return f"{parsed.major}.{parsed.minor}.{parsed.micro + 1}"


def _write_todo_fixture(todo: Todo) -> Path:
    safe_request = todo.request.replace(".", " ")
    slug = slugify(safe_request).replace("-", "_")
    if not slug:
        slug = "todo"
    path = TODO_FIXTURE_DIR / f"todos__{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [
        {
            "model": "core.todo",
            "fields": {
                "request": todo.request,
                "url": todo.url,
                "request_details": todo.request_details,
            },
        }
    ]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _should_use_python_changelog(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror in {193}:
        return True
    return exc.errno in {errno.ENOEXEC, errno.EACCES, errno.ENOENT}


def _generate_changelog_with_python(log_path: Path) -> None:
    _append_log(log_path, "Falling back to Python changelog generator")
    changelog_path = Path("CHANGELOG.rst")
    describe = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        capture_output=True,
        text=True,
        check=False,
    )
    start_tag = describe.stdout.strip() if describe.returncode == 0 else ""
    range_spec = f"{start_tag}..HEAD" if start_tag else "HEAD"
    previous = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else None
    sections = changelog_utils.collect_sections(range_spec=range_spec, previous_text=previous)
    content = changelog_utils.render_changelog(sections)
    if not content.endswith("\n"):
        content += "\n"
    changelog_path.write_text(content, encoding="utf-8")
    _append_log(log_path, "Regenerated CHANGELOG.rst using Python fallback")


def _ensure_release_todo(release) -> tuple[Todo, Path]:
    target_version = _next_patch_version(release.version)
    request = f"Create release {release.package.name} {target_version}"
    try:
        url = reverse("admin:core_packagerelease_changelist")
    except NoReverseMatch:
        url = ""
    todo, _ = Todo.all_objects.update_or_create(
        request__iexact=request,
        defaults={
            "request": request,
            "url": url,
            "request_details": "",
            "is_seed_data": True,
            "is_deleted": False,
            "is_user_data": False,
            "done_on": None,
            "on_done_condition": "",
        },
    )
    fixture_path = _write_todo_fixture(todo)
    return todo, fixture_path


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
            repo_version = Version(version_path.read_text(encoding="utf-8").strip())
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


def _step_check_todos(release, ctx, log_path: Path) -> None:
    pending_qs = Todo.objects.filter(is_deleted=False, done_on__isnull=True)
    if pending_qs.exists():
        ctx["todos"] = list(
            pending_qs.values("id", "request", "url", "request_details")
        )
        if not ctx.get("todos_ack"):
            raise PendingTodos()
    todos = list(Todo.objects.filter(is_deleted=False))
    for todo in todos:
        todo.delete()
    removed = []
    for path in TODO_FIXTURE_DIR.glob("todos__*.json"):
        removed.append(str(path))
        path.unlink()
    if removed:
        subprocess.run(["git", "add", *removed], check=False)
        subprocess.run(
            ["git", "commit", "-m", "chore: remove TODO fixtures"],
            check=False,
        )
    ctx.pop("todos", None)
    ctx["todos_ack"] = True


def _step_check_version(release, ctx, log_path: Path) -> None:
    from . import release as release_utils
    from packaging.version import InvalidVersion, Version

    if not release_utils._git_clean():
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        files = [line[3:] for line in proc.stdout.splitlines()]
        fixture_files = [
            f
            for f in files
            if "fixtures" in Path(f).parts and Path(f).suffix == ".json"
        ]
        if not files or len(fixture_files) != len(files):
            raise Exception("Git repository is not clean")

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
                        {obj.get("model", "") for obj in data if isinstance(obj, dict)}
                    )
                elif isinstance(data, dict):
                    count = 1
                    models = [data.get("model", "")]
                else:  # pragma: no cover - unexpected structure
                    count = 0
                    models = []
            summary.append({"path": f, "count": count, "models": models})

        ctx["fixtures"] = summary
        _append_log(
            log_path,
            "Committing fixture changes: " + ", ".join(fixture_files),
        )
        subprocess.run(["git", "add", *fixture_files], check=True)
        subprocess.run(["git", "commit", "-m", "chore: update fixtures"], check=True)
        _append_log(log_path, "Fixture changes committed")

    version_path = Path("VERSION")
    if version_path.exists():
        current = version_path.read_text(encoding="utf-8").strip()
        if current and Version(release.version) < Version(current):
            raise Exception(
                f"Version {release.version} is older than existing {current}"
            )

    _append_log(log_path, f"Checking if version {release.version} exists on PyPI")
    if release_utils.network_available():
        try:
            resp = requests.get(f"https://pypi.org/pypi/{release.package.name}/json")
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


def _step_handle_migrations(release, ctx, log_path: Path) -> None:
    _append_log(log_path, "Freeze, squash and approve migrations")
    _append_log(log_path, "Migration review acknowledged (manual step)")


def _step_changelog_docs(release, ctx, log_path: Path) -> None:
    _append_log(log_path, "Compose CHANGELOG and documentation")
    _append_log(log_path, "CHANGELOG and documentation review recorded")


def _step_pre_release_actions(release, ctx, log_path: Path) -> None:
    _append_log(log_path, "Execute pre-release actions")
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
    if notes != release.changelog:
        release.changelog = notes
        release.save(update_fields=["changelog"])
        PackageRelease.dump_fixture()
        _append_log(log_path, f"Recorded changelog notes for v{release.version}")
    subprocess.run(["git", "add", "CHANGELOG.rst"], check=True)
    _append_log(log_path, "Staged CHANGELOG.rst for commit")
    version_path = Path("VERSION")
    version_path.write_text(f"{release.version}\n", encoding="utf-8")
    _append_log(log_path, f"Updated VERSION file to {release.version}")
    subprocess.run(["git", "add", "VERSION"], check=True)
    _append_log(log_path, "Staged VERSION for commit")
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--cached",
            "--quiet",
            "--",
            "CHANGELOG.rst",
            "VERSION",
        ],
        check=False,
    )
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
    todo, fixture_path = _ensure_release_todo(release)
    fixture_display = _format_path(fixture_path)
    _append_log(log_path, f"Added TODO: {todo.request}")
    _append_log(log_path, f"Wrote TODO fixture {fixture_display}")
    subprocess.run(["git", "add", str(fixture_path)], check=True)
    _append_log(log_path, f"Staged TODO fixture {fixture_display}")
    fixture_diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", str(fixture_path)],
        check=False,
    )
    if fixture_diff.returncode != 0:
        commit_message = f"chore: add release TODO for {release.package.name}"
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        _append_log(log_path, f"Committed TODO fixture {fixture_display}")
    else:
        _append_log(
            log_path,
            f"No changes detected for TODO fixture {fixture_display}; skipping commit",
        )
    _append_log(log_path, "Pre-release actions complete")


def _step_run_tests(release, ctx, log_path: Path) -> None:
    _append_log(log_path, "Complete test suite with --all flag")
    _append_log(log_path, "Test suite completion acknowledged")


def _step_promote_build(release, ctx, log_path: Path) -> None:
    from . import release as release_utils

    _append_log(log_path, "Generating build files")
    try:
        try:
            subprocess.run(["git", "fetch", "origin", "main"], check=True)
            _append_log(log_path, "Fetched latest changes from origin/main")
            subprocess.run(["git", "rebase", "origin/main"], check=True)
            _append_log(log_path, "Rebased current branch onto origin/main")
        except subprocess.CalledProcessError as exc:
            subprocess.run(["git", "rebase", "--abort"], check=False)
            _append_log(log_path, "Rebase onto origin/main failed; aborted rebase")
            raise Exception("Rebase onto main failed") from exc
        release_utils.promote(
            package=release.to_package(),
            version=release.version,
            creds=release.to_credentials(),
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
        subprocess.run(["git", "push"], check=True)
        _append_log(log_path, "Pushed release changes to origin")
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


def _step_release_manager_approval(release, ctx, log_path: Path) -> None:
    if release.to_credentials() is None:
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


def _step_publish(release, ctx, log_path: Path) -> None:
    from . import release as release_utils

    _append_log(log_path, "Uploading distribution")
    release_utils.publish(
        package=release.to_package(),
        version=release.version,
        creds=release.to_credentials(),
    )
    release.pypi_url = (
        f"https://pypi.org/project/{release.package.name}/{release.version}/"
    )
    release.release_on = timezone.now()
    release.save(update_fields=["pypi_url", "release_on"])
    PackageRelease.dump_fixture()
    _append_log(log_path, f"Recorded PyPI URL: {release.pypi_url}")
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

    user = authenticate(request, rfid=rfid)
    if user is None:
        return JsonResponse({"detail": "invalid RFID"}, status=401)

    login(request, user)
    return JsonResponse({"id": user.id, "username": user.username})


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
    """Create a live subscription for an energy account from POSTed JSON."""

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
        account = EnergyAccount.objects.get(id=account_id)
    except EnergyAccount.DoesNotExist:
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
        account = EnergyAccount.objects.select_related("live_subscription_product").get(
            id=account_id
        )
    except EnergyAccount.DoesNotExist:
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
        tags = [
            {
                "rfid": t.rfid,
                "custom_label": t.custom_label,
                "energy_accounts": list(t.energy_accounts.values_list("id", flat=True)),
                "external_command": t.external_command,
                "allowed": t.allowed,
                "color": t.color,
                "released": t.released,
            }
            for t in qs.order_by("rfid")
        ]
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
            energy_accounts = row.get("energy_accounts") or []
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

            tag, _ = RFID.objects.update_or_create(
                rfid=rfid.upper(),
                defaults={
                    "allowed": allowed,
                    "color": color,
                    "released": released,
                    "custom_label": custom_label,
                    "external_command": external_command,
                },
            )
            if energy_accounts:
                tag.energy_accounts.set(
                    EnergyAccount.objects.filter(id__in=energy_accounts)
                )
            else:
                tag.energy_accounts.clear()
            count += 1

        return JsonResponse({"imported": count})

    return JsonResponse({"detail": "GET or POST required"}, status=400)


@staff_member_required
def release_progress(request, pk: int, action: str):
    release = get_object_or_404(PackageRelease, pk=pk)
    if action != "publish":
        raise Http404("Unknown action")
    session_key = f"release_publish_{pk}"
    lock_path = Path("locks") / f"release_publish_{pk}.json"
    restart_path = Path("locks") / f"release_publish_{pk}.restarts"

    if not release.is_current:
        if release.is_published:
            raise Http404("Release is not current")
        updated, previous_version = _sync_release_with_revision(release)
        if updated:
            request.session.pop(session_key, None)
            if lock_path.exists():
                lock_path.unlink()
            if restart_path.exists():
                restart_path.unlink()
            log_dir = Path("logs")
            pattern = f"pr.{release.package.name}.v{previous_version}*.log"
            for log_file in log_dir.glob(pattern):
                log_file.unlink()
        if not release.is_current:
            raise Http404("Release is not current")

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
        log_dir = Path("logs")
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

    manager = release.release_manager or release.package.release_manager
    credentials_ready = bool(release.to_credentials())
    if credentials_ready and ctx.get("approval_credentials_missing"):
        ctx.pop("approval_credentials_missing", None)

    ack_todos_requested = bool(request.GET.get("ack_todos"))

    if request.GET.get("start"):
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
    if request.GET.get("pause") and ctx.get("started"):
        ctx["paused"] = True
    restart_count = 0
    if restart_path.exists():
        try:
            restart_count = int(restart_path.read_text(encoding="utf-8"))
        except Exception:
            restart_count = 0
    step_count = ctx.get("step", 0)
    step_param = request.GET.get("step")

    pending_qs = Todo.objects.filter(is_deleted=False, done_on__isnull=True)
    pending_items = list(pending_qs)
    if ack_todos_requested:
        if pending_items:
            failures = []
            for todo in pending_items:
                result = todo.check_on_done_condition()
                if not result.passed:
                    failures.append((todo, result))
            if failures:
                ctx.pop("todos_ack", None)
                for todo, result in failures:
                    messages.error(request, _format_condition_failure(todo, result))
            else:
                ctx["todos_ack"] = True
        else:
            ctx["todos_ack"] = True

    if pending_items and not ctx.get("todos_ack"):
        ctx["todos"] = [
            {
                "id": todo.pk,
                "request": todo.request,
                "url": todo.url,
                "request_details": todo.request_details,
            }
            for todo in pending_items
        ]
    else:
        ctx.pop("todos", None)

    log_name = _release_log_name(release.package.name, release.version)
    if ctx.get("log") != log_name:
        ctx = {
            "step": 0,
            "log": log_name,
            "started": ctx.get("started", False),
        }
        step_count = 0
    log_path = Path("logs") / log_name
    ctx.setdefault("log", log_name)
    ctx.setdefault("paused", False)

    if (
        ctx.get("started")
        and step_count == 0
        and (step_param is None or step_param == "0")
    ):
        if log_path.exists():
            log_path.unlink()

    steps = PUBLISH_STEPS
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
                func(release, ctx, log_path)
            except PendingTodos:
                pass
            except ApprovalRequired:
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
    has_pending_todos = bool(ctx.get("todos") and not ctx.get("todos_ack"))
    if has_pending_todos:
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
        "todos": ctx.get("todos"),
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
        "can_resume": can_resume,
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
def world_simulator_client(request, pk: int):
    """Render the built-in Evennia client for the requested simulator."""

    simulator = get_object_or_404(
        WorldSimulator, pk=pk, is_deleted=False
    )
    session = simulator.prepare_client_session(request.user)
    simulator.schedule_watchdog()
    context = {
        "simulator": simulator,
        "client_url": simulator.client_url,
        "username": request.user.get_username(),
        "account_username": session.account_username if session else None,
    }
    response = render(request, "core/world_simulator_client.html", context)
    if session:
        cookie_kwargs: dict[str, object] = {
            "httponly": True,
            "path": "/webclient/",
        }
        domain = simulator.session_cookie_domain
        if domain:
            cookie_kwargs["domain"] = domain
        secure = simulator.client_url.lower().startswith("https://")
        cookie_kwargs["secure"] = secure
        cookie_kwargs["samesite"] = "None" if secure else "Lax"
        response.set_cookie(
            "sessionid",
            session.session_key,
            max_age=getattr(settings, "SESSION_COOKIE_AGE", 1209600),
            **cookie_kwargs,
        )
    return response


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
    todo.save(update_fields=["done_on"])
    return redirect(redirect_to)
