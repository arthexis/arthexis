"""Staff dashboard views for repository work."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render, resolve_url
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.nodes.models import Node
from apps.repos import github
from apps.repos.github_monitor import local_node_role
from apps.repos.models import (
    GitHubMonitorItem,
    GitHubMonitorTask,
    GitHubRepository,
    RepositoryIssue,
    RepositoryPullRequest,
    RepositoryWorkAssignment,
    RepositoryWorkNodeSnapshot,
)
from apps.repos.permissions import (
    REPOSITORY_WORK_ASSIGNMENT_ADD_PERMISSIONS,
    REPOSITORY_WORK_ASSIGNMENT_CHANGE_PERMISSIONS,
    REPOSITORY_WORK_ASSIGNMENT_PERMISSIONS,
    REPOSITORY_WORK_LABEL_PERMISSIONS,
    REPOSITORY_WORK_SYNC_PERMISSIONS,
    REPOSITORY_WORK_VIEW_PERMISSIONS,
)
from apps.repos.services import github as github_service
from apps.repos.services import work_assignments

REPOSITORY_WORK_DASHBOARD_REFRESH_SECONDS = 60
GitHubItem = Mapping[str, object]
LABEL_ACTIONS = {"add-label", "remove-label"}
ASSIGNMENT_ACTIONS = {
    "assign-local",
    "authorize-local-patchwork",
    "remove-local-assignment",
}


@dataclass(frozen=True)
class RepositoryWorkRow:
    item: RepositoryIssue | RepositoryPullRequest
    monitor_items: tuple[GitHubMonitorItem, ...]
    target_type: str
    assignment: RepositoryWorkAssignment | None = None

    @property
    def monitor_statuses(self) -> str:
        return ", ".join(
            str(monitor_item.get_status_display())
            for monitor_item in self.monitor_items
        )

    @property
    def monitor_url(self) -> str:
        if not self.monitor_items:
            return ""
        return reverse(
            "admin:repos_githubmonitoritem_change",
            args=[self.monitor_items[0].pk],
        )

    @property
    def label_names(self) -> tuple[str, ...]:
        labels = self.item.labels if isinstance(self.item.labels, list) else []
        return tuple(str(label).strip() for label in labels if str(label).strip())

    @property
    def assigned_to_local_node(self) -> bool:
        return self.assignment is not None and self.assignment.is_assigned

    @property
    def local_patchwork_authorized(self) -> bool:
        if self.assignment is None or not self.assignment.is_active:
            return False
        if (
            _repository_work_node_role_name(self.assignment.node).casefold()
            != "control"
        ):
            return True
        reason = str(self.assignment.reason or "").casefold()
        return work_assignments.CONTROL_MANUAL_PATCHWORK_REASON_MARKER in reason


def _configured_repositories() -> list[GitHubRepository]:
    return list(GitHubRepository.objects.order_by("owner", "name", "pk"))


def _repository_work_node_role_name(node: Node) -> str:
    local_node = Node.get_local()
    if local_node is not None and local_node.pk == node.pk:
        live_role = str(local_node_role() or "").strip()
        if live_role:
            return live_role
    try:
        capabilities = node.repository_work_snapshot.capabilities
    except RepositoryWorkNodeSnapshot.DoesNotExist:
        capabilities = {}
    if isinstance(capabilities, Mapping):
        reported_role = str(capabilities.get("node_role") or "").strip()
        if reported_role:
            return reported_role
    return str(getattr(getattr(node, "role", None), "name", "") or "").strip()


def _default_repository(
    repositories: Iterable[GitHubRepository],
) -> GitHubRepository | None:
    repository_list = list(repositories)
    if not repository_list:
        return None

    try:
        active_repository = GitHubRepository.resolve_active_repository()
    except Exception:
        active_repository = None

    if active_repository is not None:
        for repository in repository_list:
            if (
                repository.owner == active_repository.owner
                and repository.name == active_repository.name
            ):
                return repository

    return repository_list[0]


def _selected_repository(
    request,
    repositories: list[GitHubRepository],
) -> GitHubRepository | None:
    repository_id = (
        request.POST.get("repository") if request.method == "POST" else None
    ) or request.GET.get("repository")
    if repository_id:
        for repository in repositories:
            if str(repository.pk) == repository_id:
                return repository
        return None
    return _default_repository(repositories)


def _monitor_map(
    repository: GitHubRepository,
    *,
    issue_numbers: list[int] | None = None,
):
    monitor_items = GitHubMonitorItem.objects.filter(
        task__repository=repository,
    )
    if issue_numbers is not None:
        monitor_items = monitor_items.filter(issue_number__in=issue_numbers)
    mapped_items: dict[tuple[str, int], list[GitHubMonitorItem]] = defaultdict(list)
    for monitor_item in monitor_items:
        mapped_items[(monitor_item.target_type, monitor_item.issue_number)].append(
            monitor_item
        )
    return mapped_items


def _local_node() -> Node | None:
    return Node.get_local()


def _assignment_nodes() -> list[Node]:
    return list(Node.objects.select_related("role").order_by("hostname", "pk"))


def _selected_assignment_node(
    request,
    nodes: list[Node],
    local_node: Node | None,
) -> Node | None:
    node_id = (
        request.POST.get("assignment_node") if request.method == "POST" else None
    ) or request.GET.get("assignment_node")
    if node_id:
        for node in nodes:
            if str(node.pk) == str(node_id):
                return node
        return None
    if local_node is not None:
        for node in nodes:
            if node.pk == local_node.pk:
                return node
    return nodes[0] if nodes else None


def _assignment_map(
    repository: GitHubRepository,
    *,
    node: Node | None,
    issue_numbers: list[int] | None = None,
) -> dict[tuple[str, int], RepositoryWorkAssignment]:
    if node is None:
        return {}
    assignments = RepositoryWorkAssignment.objects.filter(
        repository=repository,
        node=node,
        status__in=(
            RepositoryWorkAssignment.Status.ASSIGNED,
            RepositoryWorkAssignment.Status.ACTIVE,
        ),
    )
    if issue_numbers is not None:
        assignments = assignments.filter(number__in=issue_numbers)
    return {
        (assignment.target_type, assignment.number): assignment
        for assignment in assignments
    }


def _work_rows(
    items: Iterable[RepositoryIssue | RepositoryPullRequest],
    *,
    monitor_items_by_key,
    assignments_by_key,
    target_type: str,
) -> list[RepositoryWorkRow]:
    rows = []
    for item in items:
        monitor_items = tuple(monitor_items_by_key.get((target_type, item.number), ()))
        assignment = assignments_by_key.get((target_type, item.number))
        rows.append(
            RepositoryWorkRow(
                item=item,
                monitor_items=monitor_items,
                target_type=target_type,
                assignment=assignment,
            )
        )
    return rows


def _github_work_defaults(item: GitHubItem) -> dict[str, object]:
    return {
        "title": item.get("title") or "",
        "state": item.get("state") or "",
        "html_url": item.get("html_url") or "",
        "api_url": item.get("url") or "",
        "author": (item.get("user") or {}).get("login") or "",
        "labels": github.extract_label_names(item),
        "created_at": github.parse_github_timestamp(item.get("created_at")),
        "updated_at": github.parse_github_timestamp(item.get("updated_at")),
    }


def _pull_request_defaults(item: GitHubItem) -> dict[str, object]:
    defaults = _github_work_defaults(item)
    defaults.update(
        {
            "merged_at": (
                github.parse_github_timestamp(item.get("merged_at"))
                if item.get("merged_at")
                else None
            ),
            "source_branch": (item.get("head") or {}).get("ref") or "",
            "target_branch": (item.get("base") or {}).get("ref") or "",
            "is_draft": bool(item.get("draft")),
        }
    )
    return defaults


def _sync_repository_items(
    *,
    repository: GitHubRepository,
    token: str,
    state: str,
    fetch_items: Callable[..., Iterable[GitHubItem]],
    model: type[RepositoryIssue] | type[RepositoryPullRequest],
    defaults_for: Callable[[GitHubItem], dict[str, object]],
    skip_item: Callable[[GitHubItem], bool] | None = None,
) -> tuple[int, int]:
    repo_obj = github.ensure_repository(repository)
    created = 0
    updated = 0

    for item in fetch_items(
        token=token,
        owner=repo_obj.owner,
        name=repo_obj.name,
        state=state,
    ):
        if skip_item is not None and skip_item(item):
            continue
        number = item.get("number")
        if not isinstance(number, int):
            continue

        _, was_created = model.objects.update_or_create(
            repository=repo_obj,
            number=number,
            defaults=defaults_for(item),
        )
        if was_created:
            created += 1
        else:
            updated += 1

    return created, updated


def _sync_issues_from_github(
    *,
    repository: GitHubRepository,
    token: str,
    state: str,
) -> tuple[int, int]:
    return _sync_repository_items(
        repository=repository,
        token=token,
        state=state,
        fetch_items=github_service.fetch_repository_issues,
        model=RepositoryIssue,
        defaults_for=_github_work_defaults,
        skip_item=lambda item: "pull_request" in item,
    )


def _sync_pull_requests_from_github(
    *,
    repository: GitHubRepository,
    token: str,
    state: str,
) -> tuple[int, int]:
    return _sync_repository_items(
        repository=repository,
        token=token,
        state=state,
        fetch_items=github_service.fetch_repository_pull_requests,
        model=RepositoryPullRequest,
        defaults_for=_pull_request_defaults,
    )


def _resolve_work_token(request) -> str:
    token = github_service.resolve_configured_token(user=request.user)
    if not token:
        raise github_service.GitHubRepositoryError("GitHub token is not configured")
    return token


def _sync_repository_work(request, repository: GitHubRepository):
    if not request.user.has_perms(REPOSITORY_WORK_SYNC_PERMISSIONS):
        raise PermissionDenied

    try:
        token = _resolve_work_token(request)
        issue_created, issue_updated = _sync_issues_from_github(
            repository=repository,
            token=token,
            state="all",
        )
        pr_created, pr_updated = _sync_pull_requests_from_github(
            repository=repository,
            token=token,
            state="all",
        )
    except Exception as exc:
        messages.error(
            request,
            _("Unable to sync repository work from GitHub: %(error)s") % {"error": exc},
        )
        return

    messages.success(
        request,
        _(
            "Synced %(issue_created)s new and %(issue_updated)s updated issues; "
            "%(pr_created)s new and %(pr_updated)s updated pull requests."
        )
        % {
            "issue_created": issue_created,
            "issue_updated": issue_updated,
            "pr_created": pr_created,
            "pr_updated": pr_updated,
        },
    )


def _repository_label_names(repository: GitHubRepository, token: str) -> set[str]:
    repo_obj = github.ensure_repository(repository)
    names: set[str] = set()
    for label in github_service.fetch_repository_labels(
        token=token,
        owner=repo_obj.owner,
        name=repo_obj.name,
    ):
        raw_name = label.get("name") if isinstance(label, Mapping) else label
        name = str(raw_name or "").strip()
        if name:
            names.add(name)
    return names


def _work_item_for_label_action(
    repository: GitHubRepository,
    *,
    target_type: str,
    number: str,
) -> RepositoryIssue | RepositoryPullRequest | None:
    try:
        item_number = int(number)
    except (TypeError, ValueError):
        return None

    if target_type == GitHubMonitorTask.TargetType.ISSUE:
        return RepositoryIssue.objects.filter(
            repository=repository,
            number=item_number,
        ).first()
    if target_type == GitHubMonitorTask.TargetType.PULL_REQUEST:
        return RepositoryPullRequest.objects.filter(
            repository=repository,
            number=item_number,
        ).first()
    return None


def _assignment_target_type_for_item(
    item: RepositoryIssue | RepositoryPullRequest,
) -> RepositoryWorkAssignment.TargetType:
    if isinstance(item, RepositoryIssue):
        return RepositoryWorkAssignment.TargetType.ISSUE
    return RepositoryWorkAssignment.TargetType.PULL_REQUEST


def _store_label_update(
    item: RepositoryIssue | RepositoryPullRequest,
    *,
    label: str,
    action: str,
) -> None:
    raw_labels = item.labels if isinstance(item.labels, list) else []
    labels = list(
        dict.fromkeys(str(name).strip() for name in raw_labels if str(name).strip())
    )
    if action == "add-label":
        if github_service.is_issue_priority_label(label):
            labels = github_service.collapse_issue_priority_labels([*labels, label])
        elif label not in labels:
            labels.append(label)
    elif action == "remove-label":
        labels = [name for name in labels if name != label]
    item.labels = labels
    item.save(update_fields=["labels"])


def _priority_labels_to_replace(
    label: str,
    known_labels: Iterable[str],
) -> list[str]:
    if not github_service.is_issue_priority_label(label):
        return []

    return sorted(
        cleaned_name
        for name in known_labels
        if (cleaned_name := str(name).strip())
        and cleaned_name != label
        and github_service.is_issue_priority_label(cleaned_name)
    )


def _remove_replaced_priority_labels(
    repository: GitHubRepository,
    item: RepositoryIssue | RepositoryPullRequest,
    *,
    token: str,
    label: str,
    known_labels: Iterable[str],
) -> None:
    for existing_priority_label in _priority_labels_to_replace(label, known_labels):
        github_service.remove_issue_label(
            owner=repository.owner,
            repository=repository.name,
            issue_number=item.number,
            token=token,
            label=existing_priority_label,
            ignore_missing=True,
        )


def _mutate_repository_work_label(
    request,
    repository: GitHubRepository,
    *,
    action: str,
) -> None:
    if not request.user.has_perms(REPOSITORY_WORK_LABEL_PERMISSIONS):
        raise PermissionDenied

    label = str(request.POST.get("label") or "").strip()
    if not label:
        messages.error(request, _("Enter a repository label."))
        return

    item = _work_item_for_label_action(
        repository,
        target_type=str(request.POST.get("target_type") or ""),
        number=str(request.POST.get("number") or ""),
    )
    if item is None:
        messages.error(
            request,
            _(
                "Select an issue or pull request from this repository before changing labels."
            ),
        )
        return

    try:
        token = _resolve_work_token(request)
        if action == "add-label":
            known_labels = _repository_label_names(repository, token)
            if label not in known_labels:
                messages.error(
                    request,
                    _('Label "%(label)s" is not configured for %(repository)s.')
                    % {"label": label, "repository": repository.slug},
                )
                return
            _remove_replaced_priority_labels(
                repository,
                item,
                token=token,
                label=label,
                known_labels=known_labels,
            )
            github_service.add_issue_labels(
                owner=repository.owner,
                repository=repository.name,
                issue_number=item.number,
                token=token,
                labels=(label,),
            )
            success_message = _('Added "%(label)s" to #%(number)s.')
        else:
            github_service.remove_issue_label(
                owner=repository.owner,
                repository=repository.name,
                issue_number=item.number,
                token=token,
                label=label,
            )
            success_message = _('Removed "%(label)s" from #%(number)s.')
    except Exception as exc:
        messages.error(
            request,
            _("Unable to update repository label: %(error)s") % {"error": exc},
        )
        return

    _store_label_update(item, label=label, action=action)
    messages.success(
        request,
        success_message % {"label": label, "number": item.number},
    )


def _non_removed_assignment(
    *,
    repository: GitHubRepository,
    target_type: str,
    number: int,
    node: Node,
) -> RepositoryWorkAssignment | None:
    return (
        RepositoryWorkAssignment.objects.filter(
            repository=repository,
            target_type=target_type,
            number=number,
            node=node,
        )
        .exclude(status=RepositoryWorkAssignment.Status.REMOVED)
        .first()
    )


def _require_assignment_change_for_existing(
    request,
    assignment: RepositoryWorkAssignment | None,
) -> None:
    if assignment is not None and not request.user.has_perms(
        REPOSITORY_WORK_ASSIGNMENT_CHANGE_PERMISSIONS
    ):
        raise PermissionDenied


def _mutate_repository_work_assignment(
    request,
    repository: GitHubRepository,
    *,
    action: str,
    node: Node | None,
) -> None:
    if action == "assign-local" and not request.user.has_perms(
        REPOSITORY_WORK_ASSIGNMENT_ADD_PERMISSIONS
    ):
        raise PermissionDenied
    if action in {
        "authorize-local-patchwork",
        "remove-local-assignment",
    } and not request.user.has_perms(REPOSITORY_WORK_ASSIGNMENT_CHANGE_PERMISSIONS):
        raise PermissionDenied

    if node is None:
        messages.error(request, _("No target node is available for work assignment."))
        return

    post_target_type = str(request.POST.get("target_type") or "")
    item = _work_item_for_label_action(
        repository,
        target_type=post_target_type,
        number=str(request.POST.get("number") or ""),
    )
    if item is None:
        messages.error(
            request,
            _(
                "Select an issue or pull request from this repository before changing assignments."
            ),
        )
        return
    target_type = _assignment_target_type_for_item(item)

    if action == "assign-local":
        _require_assignment_change_for_existing(
            request,
            _non_removed_assignment(
                repository=repository,
                target_type=target_type,
                number=item.number,
                node=node,
            ),
        )
        assignment, _created = RepositoryWorkAssignment.objects.update_or_create(
            repository=repository,
            target_type=target_type,
            number=item.number,
            node=node,
            defaults={
                "assigned_by": request.user,
                "assigned_at": timezone.now(),
                "patchwork_authorized": False,
                "reason": str(_("Assigned from the repository work dashboard.")),
                "status": RepositoryWorkAssignment.Status.ASSIGNED,
            },
        )
        messages.success(
            request,
            _("Assigned #%(number)s to %(node)s.")
            % {"number": item.number, "node": assignment.node},
        )
        return

    if action == "authorize-local-patchwork":
        assignment = RepositoryWorkAssignment.objects.filter(
            repository=repository,
            target_type=target_type,
            number=item.number,
            node=node,
            status__in=(
                RepositoryWorkAssignment.Status.ASSIGNED,
                RepositoryWorkAssignment.Status.ACTIVE,
            ),
        ).first()
        if assignment is None:
            messages.error(
                request,
                _("Assign #%(number)s before authorizing local patchwork.")
                % {"number": item.number},
            )
            return
        reason = str(
            _("Authorized for local patchwork from the repository work dashboard.")
        )
        if _repository_work_node_role_name(node).casefold() == "control":
            reason = work_assignments.control_manual_patchwork_reason(reason)
        assignment.patchwork_authorized = True
        assignment.reason = reason
        assignment.status = RepositoryWorkAssignment.Status.ACTIVE
        assignment.updated_at = timezone.now()
        assignment.save(
            update_fields=[
                "patchwork_authorized",
                "reason",
                "status",
                "updated_at",
            ],
        )
        messages.success(
            request,
            _("Authorized local patchwork for #%(number)s on %(node)s.")
            % {"number": item.number, "node": assignment.node},
        )
        return

    updated = RepositoryWorkAssignment.objects.filter(
        repository=repository,
        target_type=target_type,
        number=item.number,
        node=node,
    ).update(
        patchwork_authorized=False,
        status=RepositoryWorkAssignment.Status.REMOVED,
        updated_at=timezone.now(),
    )
    if updated:
        messages.success(
            request,
            _("Removed the local assignment for #%(number)s.")
            % {"number": item.number},
        )
    else:
        messages.info(
            request,
            _("No local assignment existed for #%(number)s.") % {"number": item.number},
        )


def _user_can_view_repository_work_dashboard(user) -> bool:
    return getattr(user, "is_staff", False) and user.has_perms(
        REPOSITORY_WORK_VIEW_PERMISSIONS
    )


@require_http_methods(["GET", "POST"])
def repository_work_dashboard(request):
    """Show stored repository issues and PRs side by side."""

    if not getattr(request.user, "is_authenticated", False):
        return redirect_to_login(
            request.get_full_path(),
            login_url=resolve_url(settings.LOGIN_URL),
        )
    can_view_staff_dashboard = _user_can_view_repository_work_dashboard(request.user)
    if not can_view_staff_dashboard:
        raise PermissionDenied

    repositories = _configured_repositories()
    selected_repository = _selected_repository(request, repositories)
    local_node = _local_node()
    assignment_nodes = _assignment_nodes()
    selected_assignment_node = _selected_assignment_node(
        request,
        assignment_nodes,
        local_node,
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if selected_repository is not None and action == "sync":
            _sync_repository_work(request, selected_repository)
        elif selected_repository is not None and action in LABEL_ACTIONS:
            _mutate_repository_work_label(
                request,
                selected_repository,
                action=str(action),
            )
        elif selected_repository is not None and action in ASSIGNMENT_ACTIONS:
            _mutate_repository_work_assignment(
                request,
                selected_repository,
                action=str(action),
                node=selected_assignment_node,
            )
        redirect_url = reverse("repos:repository-work-dashboard")
        if selected_repository is not None:
            redirect_url = f"{redirect_url}?repository={selected_repository.pk}"
            if selected_assignment_node is not None:
                redirect_url = (
                    f"{redirect_url}&assignment_node={selected_assignment_node.pk}"
                )
        return redirect(redirect_url)

    issues = []
    pull_requests = []
    issue_rows = []
    pull_request_rows = []

    if selected_repository is not None:
        issues = list(
            RepositoryIssue.objects.filter(
                repository=selected_repository,
                state="open",
            ).order_by("-updated_at", "-created_at", "-number")
        )
        pull_requests = list(
            RepositoryPullRequest.objects.filter(
                repository=selected_repository,
                state="open",
            ).order_by(
                "-updated_at",
                "-created_at",
                "-number",
            )
        )
        issue_numbers = [issue.number for issue in issues] + [
            pull_request.number for pull_request in pull_requests
        ]
        monitor_items_by_key = _monitor_map(
            selected_repository,
            issue_numbers=issue_numbers,
        )
        assignments_by_key = _assignment_map(
            selected_repository,
            node=selected_assignment_node,
            issue_numbers=issue_numbers,
        )
        issue_rows = _work_rows(
            issues,
            monitor_items_by_key=monitor_items_by_key,
            assignments_by_key=assignments_by_key,
            target_type=GitHubMonitorTask.TargetType.ISSUE,
        )
        pull_request_rows = _work_rows(
            pull_requests,
            monitor_items_by_key=monitor_items_by_key,
            assignments_by_key=assignments_by_key,
            target_type=GitHubMonitorTask.TargetType.PULL_REQUEST,
        )

    context = {
        **admin_context(request),
        "title": _("Repository work"),
        "repositories": repositories,
        "selected_repository": selected_repository,
        "local_node": local_node,
        "assignment_nodes": assignment_nodes,
        "selected_assignment_node": selected_assignment_node,
        "can_sync": can_view_staff_dashboard
        and request.user.has_perms(REPOSITORY_WORK_SYNC_PERMISSIONS),
        "can_manage_labels": can_view_staff_dashboard
        and request.user.has_perms(REPOSITORY_WORK_LABEL_PERMISSIONS),
        "can_assign_work": can_view_staff_dashboard
        and request.user.has_perms(REPOSITORY_WORK_ASSIGNMENT_ADD_PERMISSIONS),
        "can_remove_assignments": can_view_staff_dashboard
        and request.user.has_perms(REPOSITORY_WORK_ASSIGNMENT_CHANGE_PERMISSIONS),
        "can_authorize_patchwork": can_view_staff_dashboard
        and request.user.has_perms(REPOSITORY_WORK_ASSIGNMENT_CHANGE_PERMISSIONS),
        "can_manage_assignments": can_view_staff_dashboard
        and request.user.has_perms(REPOSITORY_WORK_ASSIGNMENT_PERMISSIONS),
        "can_view_monitor_items": can_view_staff_dashboard
        and request.user.has_perm("repos.view_githubmonitoritem"),
        "issue_rows": issue_rows,
        "pull_request_rows": pull_request_rows,
        "issue_count": len(issues),
        "pull_request_count": len(pull_requests),
        "refresh_seconds": REPOSITORY_WORK_DASHBOARD_REFRESH_SECONDS,
    }
    return render(request, "admin/repos/repository_work_dashboard.html", context)


@staff_member_required
@require_http_methods(["GET"])
def repository_work_assignment_snapshot(request):
    """Return the staff-visible assignment snapshot for upstream operators."""

    if not request.user.has_perms(REPOSITORY_WORK_VIEW_PERMISSIONS):
        raise PermissionDenied
    payload = work_assignments.local_developer_snapshot()
    local_node = _local_node()
    payload["assignments"] = (
        work_assignments.assignments_for_node(
            local_node,
            capabilities=payload.get("capabilities"),
        )
        if local_node is not None
        else []
    )
    return JsonResponse(payload)


@csrf_exempt  # NOSONAR - node sync uses a shared header token, not browser cookies.
@require_http_methods(["POST"])
def repository_work_assignment_sync(request):
    """Receive a downstream node report and return assignments targeted to it."""

    token_header = request.headers.get(
        work_assignments.ASSIGNMENT_SYNC_HEADER,
        "",
    ) or request.headers.get("Authorization", "")
    if not work_assignments.assignment_sync_token_authorized(token_header):
        return JsonResponse({"detail": "unauthorized"}, status=401)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"detail": "invalid json"}, status=400)
    if not isinstance(payload, Mapping):
        return JsonResponse({"detail": "invalid sync payload"}, status=400)
    try:
        response = work_assignments.upstream_sync_response(
            payload,
            upstream_url=request.build_absolute_uri(),
        )
    except work_assignments.AssignmentSyncError:
        return JsonResponse({"detail": "invalid sync payload"}, status=400)
    return JsonResponse(response)


def admin_context(request) -> dict[str, object]:
    """Return admin template context without requiring an admin model wrapper."""

    from django.contrib import admin

    return admin.site.each_context(request)
