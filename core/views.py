import json
import shutil
from datetime import date, timedelta

import requests
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from pathlib import Path
import subprocess

from utils.api import api_login_required

from .models import Product, Subscription, EnergyAccount, PackageRelease
from .models import RFID
from . import release as release_utils


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


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


def _step_check_pypi(release, ctx, log_path: Path) -> None:
    from . import release as release_utils
    from packaging.version import Version

    version_path = Path("VERSION")
    if version_path.exists():
        current = version_path.read_text(encoding="utf-8").strip()
        if current and Version(release.version) < Version(current):
            raise Exception(
                f"Version {release.version} is older than existing {current}"
            )
    version_path.write_text(release.version + "\n", encoding="utf-8")

    _append_log(log_path, f"Checking if version {release.version} exists on PyPI")
    if release_utils.network_available():
        try:
            resp = requests.get(
                f"https://pypi.org/pypi/{release.package.name}/json"
            )
            if resp.ok and release.version in resp.json().get("releases", {}):
                raise Exception(
                    f"Version {release.version} already on PyPI"
                )
        except Exception as exc:
            # network errors should be logged but not crash
            if "already on PyPI" in str(exc):
                raise
            _append_log(log_path, f"PyPI check failed: {exc}")
    else:
        _append_log(log_path, "Network unavailable, skipping PyPI check")


def _step_promote_build(release, ctx, log_path: Path) -> None:
    from . import release as release_utils
    release.pypi_url = f"https://pypi.org/project/{release.package.name}/{release.version}/"
    release.save(update_fields=["pypi_url"])
    PackageRelease.dump_fixture()
    _append_log(log_path, "Generating build files")
    commit_hash, branch, _current = release_utils.promote(
        package=release.to_package(),
        version=release.version,
        creds=release.to_credentials(),
    )
    release.revision = commit_hash
    release.save(update_fields=["revision"])
    ctx["branch"] = branch
    release_name = f"{release.package.name}-{release.version}-{commit_hash[:7]}"
    new_log = log_path.with_name(f"{release_name}.log")
    log_path.rename(new_log)
    ctx["log"] = new_log.name
    _append_log(new_log, "Build complete")


def _step_push_branch(release, ctx, log_path: Path) -> None:
    branch = ctx.get("branch")
    _append_log(log_path, f"Pushing branch {branch}")
    subprocess.run(["git", "push", "-u", "origin", branch], check=True)
    pr_url = None
    gh_path = shutil.which("gh")
    if gh_path:
        try:
            title = f"Release candidate for {release.version}"
            body = _changelog_notes(release.version)
            proc = subprocess.run(
                [
                    gh_path,
                    "pr",
                    "create",
                    "--title",
                    title,
                    "--body",
                    body,
                    "--base",
                    "main",
                    "--head",
                    branch,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            pr_url = proc.stdout.strip()
            ctx["pr_url"] = pr_url
            release.pr_url = pr_url
            release.save(update_fields=["pr_url"])
            _append_log(log_path, f"PR created: {pr_url}")
            cert_log = Path("logs") / "certifications.log"
            _append_log(cert_log, f"{release.version} {branch} {pr_url}")
            ctx["cert_log"] = str(cert_log)
        except Exception as exc:  # pragma: no cover - best effort
            _append_log(log_path, f"PR creation failed: {exc}")
    else:
        token = release.get_github_token()
        if token:
            try:  # pragma: no cover - best effort
                remote = subprocess.run(
                    ["git", "config", "--get", "remote.origin.url"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                repo = remote.rsplit(":", 1)[-1].split("github.com/")[-1].removesuffix(".git")
                title = f"Release candidate for {release.version}"
                body = _changelog_notes(release.version)
                resp = requests.post(
                    f"https://api.github.com/repos/{repo}/pulls",
                    json={
                        "title": title,
                        "head": branch,
                        "base": "main",
                        "body": body,
                    },
                    headers={"Authorization": f"token {token}"},
                    timeout=10,
                )
                resp.raise_for_status()
                pr_url = resp.json().get("html_url")
                if pr_url:
                    ctx["pr_url"] = pr_url
                    release.pr_url = pr_url
                    release.save(update_fields=["pr_url"])
                    _append_log(log_path, f"PR created: {pr_url}")
                    cert_log = Path("logs") / "certifications.log"
                    _append_log(cert_log, f"{release.version} {branch} {pr_url}")
                    ctx["cert_log"] = str(cert_log)
                else:
                    _append_log(log_path, "PR creation failed: no URL returned")
            except Exception as exc:
                _append_log(log_path, f"PR creation failed: {exc}")
        else:
            _append_log(
                log_path,
                "PR creation skipped: gh not installed and no GitHub token available",
            )
    subprocess.run(["git", "checkout", "main"], check=True)
    _append_log(log_path, "Branch pushed")


def _step_merge_publish(release, ctx, log_path: Path) -> None:
    from . import release as release_utils
    import time

    gh_path = shutil.which("gh")
    pr_url = ctx.get("pr_url") or release.pr_url
    if gh_path and pr_url:
        _append_log(log_path, "Waiting for PR checks")
        for _ in range(60):
            try:
                proc = subprocess.run(
                    [gh_path, "pr", "view", pr_url, "--json", "mergeable"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                state = json.loads(proc.stdout or "{}").get("mergeable")
                if state == "MERGEABLE":
                    break
            except Exception:
                pass
            time.sleep(1)
        _append_log(log_path, "Merging PR")
        try:
            subprocess.run(
                [gh_path, "pr", "merge", pr_url, "--merge", "--delete-branch"],
                check=True,
            )
            subprocess.run(["git", "pull", "--ff-only", "origin", "main"], check=True)
        except Exception as exc:
            _append_log(log_path, f"PR merge failed: {exc}")

    _append_log(log_path, "Uploading distribution")
    release_utils.publish(
        package=release.to_package(),
        version=release.version,
        creds=release.to_credentials(),
    )
    _append_log(log_path, "Upload complete")


PUBLISH_STEPS = [
    ("Check version availability", _step_check_pypi),
    ("Generate build", _step_promote_build),
    ("Push branch", _step_push_branch),
    ("Merge and publish", _step_merge_publish),
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
def add_subscription(request):
    """Create a subscription for an energy account from POSTed JSON."""

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

    sub = Subscription.objects.create(
        account_id=account_id,
        product=product,
        next_renewal=date.today() + timedelta(days=product.renewal_period),
    )
    return JsonResponse({"id": sub.id})


@api_login_required
def subscription_list(request):
    """Return subscriptions for the given account_id."""

    account_id = request.GET.get("account_id")
    if not account_id:
        return JsonResponse({"detail": "account_id required"}, status=400)

    subs = list(
        Subscription.objects.filter(account_id=account_id)
        .select_related("product")
        .values(
            "id",
            "product__name",
            "next_renewal",
        )
    )
    return JsonResponse({"subscriptions": subs})


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
            color = (
                (row.get("color") or RFID.BLACK).strip().upper() or RFID.BLACK
            )
            released = row.get("released", False)
            if isinstance(released, str):
                released = released.lower() == "true"

            tag, _ = RFID.objects.update_or_create(
                rfid=rfid.upper(),
                defaults={
                    "allowed": allowed,
                    "color": color,
                    "released": released,
                },
            )
            if energy_accounts:
                tag.energy_accounts.set(EnergyAccount.objects.filter(id__in=energy_accounts))
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
    ctx = request.session.get(session_key, {"step": 0})
    step_count = ctx.get("step", 0)
    step_param = request.GET.get("step")

    identifier = f"{release.package.name}-{release.version}"
    if release.revision:
        identifier = f"{identifier}-{release.revision[:7]}"
    log_name = ctx.get("log") or f"{identifier}.log"
    log_path = Path("logs") / log_name
    ctx.setdefault("log", log_name)

    steps = PUBLISH_STEPS
    error = ctx.get("error")

    if step_param is not None and not error and step_count < len(steps):
        to_run = int(step_param)
        if to_run == step_count:
            name, func = steps[to_run]
            try:
                func(release, ctx, log_path)
                step_count += 1
                ctx["step"] = step_count
                request.session[session_key] = ctx
            except Exception as exc:  # pragma: no cover - best effort logging
                _append_log(log_path, f"{name} failed: {exc}")
                ctx["error"] = str(exc)
                request.session[session_key] = ctx

    done = step_count >= len(steps) and not ctx.get("error")

    log_content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    next_step = step_count if not done and not ctx.get("error") else None
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
        "pr_url": ctx.get("pr_url"),
        "cert_log": ctx.get("cert_log"),
    }
    request.session[session_key] = ctx
    return render(request, "core/release_progress.html", context)
