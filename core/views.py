import json
from datetime import date, timedelta

from django.contrib.auth import authenticate, login
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from pathlib import Path
import subprocess
from django.core import serializers

from utils.api import api_login_required

from .models import Product, Subscription, EnergyAccount, PackageRelease
from .models import RFID
from . import release as release_utils


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def _step_promote_build(release, ctx, log_path: Path) -> None:
    from . import release as release_utils

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


def _step_dump_fixture(release, ctx, log_path: Path) -> None:
    _append_log(log_path, "Dumping fixture")
    data = serializers.serialize(
        "json", PackageRelease.objects.filter(is_promoted=True), indent=2
    )
    fixture_path = Path("core/fixtures/releases.json")
    fixture_path.write_text(data)
    subprocess.run(["git", "add", str(fixture_path)], check=True)
    subprocess.run(
        ["git", "commit", "-m", f"Add fixture for {release.version}"], check=True
    )
    _append_log(log_path, "Fixture committed")


def _step_push_branch(release, ctx, log_path: Path) -> None:
    branch = ctx.get("branch")
    _append_log(log_path, f"Pushing branch {branch}")
    subprocess.run(["git", "push", "-u", "origin", branch], check=True)
    subprocess.run(["git", "checkout", "main"], check=True)
    _append_log(log_path, "Branch pushed")


def _step_publish(release, ctx, log_path: Path) -> None:
    from . import release as release_utils

    _append_log(log_path, "Uploading distribution")
    release_utils.publish(
        package=release.to_package(), creds=release.to_credentials()
    )
    _append_log(log_path, "Upload complete")


PROMOTE_STEPS = [
    ("Generate build", _step_promote_build),
    ("Dump fixture", _step_dump_fixture),
    ("Push branch", _step_push_branch),
]

PUBLISH_STEPS = [("Upload to index", _step_publish)]


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
    session_key = f"release_{action}_{pk}"
    ctx = request.session.get(session_key, {})
    step = int(request.GET.get("step", ctx.get("step", 0)))

    identifier = f"{release.package.name}-{release.version}"
    if release.revision:
        identifier = f"{identifier}-{release.revision[:7]}"
    log_name = ctx.get("log") or f"{identifier}.log"
    log_path = Path("logs") / log_name
    ctx.setdefault("log", log_name)

    steps = PROMOTE_STEPS if action == "promote" else PUBLISH_STEPS
    error = ctx.get("error")

    if step < len(steps) and not error:
        name, func = steps[step]
        try:
            func(release, ctx, log_path)
            step += 1
            ctx["step"] = step
            request.session[session_key] = ctx
            return redirect(
                f"{reverse('release-progress', args=[pk, action])}?step={step}"
            )
        except Exception as exc:  # pragma: no cover - best effort logging
            _append_log(log_path, f"{name} failed: {exc}")
            ctx["error"] = str(exc)
            request.session[session_key] = ctx

    done = step >= len(steps) and not error
    if done:
        if action == "promote" and not release.is_promoted:
            release.is_promoted = True
            release.save(update_fields=["is_promoted"])
        if action == "publish" and not release.is_published:
            release.is_published = True
            release.save(update_fields=["is_published"])

    log_content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    context = {
        "release": release,
        "action": action,
        "steps": [s[0] for s in steps],
        "current_step": step,
        "done": done,
        "error": error,
        "log_content": log_content,
        "log_path": str(log_path),
    }
    return render(request, "core/release_progress.html", context)
