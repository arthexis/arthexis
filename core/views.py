import json
import shutil
from datetime import timedelta

import requests
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.urls import NoReverseMatch, reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from pathlib import Path
import subprocess
import json

from utils import revision
from utils.api import api_login_required

from .models import Product, EnergyAccount, PackageRelease, Todo
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
            [],
            {"fields": ["name"], "limit": 50},
        )
    except Exception:
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


def _clean_repo() -> None:
    """Return the git repository to a clean state."""
    subprocess.run(["git", "reset", "--hard"], check=False)
    subprocess.run(["git", "clean", "-fd"], check=False)


def _changelog_notes(version: str) -> str:
    path = Path("CHANGELOG.rst")
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    prefix = f"{version} "
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            j = i + 2
            items = []
            while j < len(lines) and lines[j].startswith("- "):
                items.append(lines[j])
                j += 1
            return "\n".join(items)
    return ""


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
    ctx.pop("todos_ack", None)


def _step_check_version(release, ctx, log_path: Path) -> None:
    from . import release as release_utils
    from packaging.version import Version

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
            if resp.ok and release.version in resp.json().get("releases", {}):
                raise Exception(f"Version {release.version} already on PyPI")
        except Exception as exc:
            # network errors should be logged but not crash
            if "already on PyPI" in str(exc):
                raise
            _append_log(log_path, f"PyPI check failed: {exc}")
    else:
        _append_log(log_path, "Network unavailable, skipping PyPI check")


def _step_handle_migrations(release, ctx, log_path: Path) -> None:
    _append_log(log_path, "Freeze, squash and approve migrations")


def _step_changelog_docs(release, ctx, log_path: Path) -> None:
    _append_log(log_path, "Compose CHANGELOG and documentation")


def _step_pre_release_actions(release, ctx, log_path: Path) -> None:
    _append_log(log_path, "Execute pre-release actions")
    version_path = Path("VERSION")
    version_path.write_text(f"{release.version}\n", encoding="utf-8")
    subprocess.run(["git", "add", "VERSION"], check=True)
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", "VERSION"],
        check=False,
    )
    if diff.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", f"pre-release commit {release.version}"],
            check=True,
        )
    else:
        _append_log(log_path, "No changes detected for VERSION; skipping commit")
        subprocess.run(["git", "reset", "HEAD", "VERSION"], check=False)


def _step_run_tests(release, ctx, log_path: Path) -> None:
    _append_log(log_path, "Complete test suite with --all flag")


def _step_promote_build(release, ctx, log_path: Path) -> None:
    from . import release as release_utils

    _append_log(log_path, "Generating build files")
    try:
        try:
            subprocess.run(["git", "fetch", "origin", "main"], check=True)
            subprocess.run(["git", "rebase", "origin/main"], check=True)
        except subprocess.CalledProcessError as exc:
            subprocess.run(["git", "rebase", "--abort"], check=False)
            raise Exception("Rebase onto main failed") from exc
        release_utils.promote(
            package=release.to_package(),
            version=release.version,
            creds=release.to_credentials(),
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
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    f"chore: update release metadata for v{release.version}",
                ],
                check=True,
            )
        subprocess.run(["git", "push"], check=True)
        PackageRelease.dump_fixture()
    except Exception:
        _clean_repo()
        raise
    release_name = f"{release.package.name}-{release.version}"
    new_log = log_path.with_name(f"{release_name}.log")
    log_path.rename(new_log)
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
    release.save(update_fields=["pypi_url"])
    PackageRelease.dump_fixture()
    _append_log(log_path, "Upload complete")


PUBLISH_STEPS = [
    ("Check version number availability", _step_check_version),
    ("Confirm release TODO completion", _step_check_todos),
    ("Freeze, squash and approve migrations", _step_handle_migrations),
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

            tag, _ = RFID.objects.update_or_create(
                rfid=rfid.upper(),
                defaults={
                    "allowed": allowed,
                    "color": color,
                    "released": released,
                    "custom_label": custom_label,
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
    if not release.is_current:
        raise Http404("Release is not current")
    session_key = f"release_publish_{pk}"
    lock_path = Path("locks") / f"release_publish_{pk}.json"
    restart_path = Path("locks") / f"release_publish_{pk}.restarts"

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
        release.save(update_fields=["pypi_url"])
        request.session.pop(session_key, None)
        if lock_path.exists():
            lock_path.unlink()
        log_dir = Path("logs")
        for f in log_dir.glob(f"{release.package.name}-{release.version}*.log"):
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

    identifier = f"{release.package.name}-{release.version}"
    log_name = f"{identifier}.log"
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
        "fixtures": ctx.get("fixtures"),
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
    return render(request, "core/release_progress.html", context)


@staff_member_required
@require_POST
def todo_done(request, pk: int):
    todo = get_object_or_404(Todo, pk=pk, is_deleted=False, done_on__isnull=True)
    result = todo.check_on_done_condition()
    if not result.passed:
        messages.error(request, _format_condition_failure(todo, result))
        return redirect("admin:index")
    todo.done_on = timezone.now()
    todo.save(update_fields=["done_on"])
    return redirect("admin:index")
