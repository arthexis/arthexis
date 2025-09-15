import json
import shutil
from datetime import date, timedelta

import requests
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _
from django.utils import timezone
from pathlib import Path
import subprocess
import json

from utils.api import api_login_required

from .models import Product, LiveSubscription, EnergyAccount, PackageRelease, Todo
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
    subprocess.run(
        ["git", "commit", "-m", f"pre-release commit {release.version}"],
        check=True,
    )


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

    sub = LiveSubscription.objects.create(
        account_id=account_id,
        product=product,
        next_renewal=date.today() + timedelta(days=product.renewal_period),
    )
    return JsonResponse({"id": sub.id})


@api_login_required
def live_subscription_list(request):
    """Return live subscriptions for the given account_id."""

    account_id = request.GET.get("account_id")
    if not account_id:
        return JsonResponse({"detail": "account_id required"}, status=400)

    subs = list(
        LiveSubscription.objects.filter(account_id=account_id)
        .select_related("product")
        .values(
            "id",
            "product__name",
            "next_renewal",
        )
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

    if request.GET.get("start"):
        ctx["started"] = True
    if request.GET.get("ack_todos"):
        ctx["todos_ack"] = True
    if request.GET.get("abort"):
        ctx["error"] = _("Publish aborted")
        request.session[session_key] = ctx
        if lock_path.exists():
            lock_path.unlink()
    restart_count = 0
    if restart_path.exists():
        try:
            restart_count = int(restart_path.read_text(encoding="utf-8"))
        except Exception:
            restart_count = 0
    step_count = ctx.get("step", 0)
    step_param = request.GET.get("step")

    pending = Todo.objects.filter(is_deleted=False, done_on__isnull=True)
    if pending.exists() and not ctx.get("todos_ack"):
        ctx["todos"] = list(pending.values("id", "request", "url", "request_details"))
    else:
        ctx.pop("todos", None)

    identifier = f"{release.package.name}-{release.version}"
    log_name = f"{identifier}.log"
    if ctx.get("log") != log_name:
        ctx = {"step": 0, "log": log_name}
        step_count = 0
    log_path = Path("logs") / log_name
    ctx.setdefault("log", log_name)

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
        step_count if ctx.get("started") and not done and not ctx.get("error") else None
    )
    if ctx.get("todos") and not ctx.get("todos_ack"):
        next_step = None
    context = {
        "release": release,
        "action": "publish",
        "steps": [s[0] for s in steps],
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
    todo.done_on = timezone.now()
    todo.save(update_fields=["done_on"])
    return redirect("admin:index")
