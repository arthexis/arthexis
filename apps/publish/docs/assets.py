import logging
import re
from html import escape
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied, SuspiciousFileOperation
from django.http import Http404
from django.urls import NoReverseMatch, reverse
from django.utils._os import safe_join


logger = logging.getLogger(__name__)


MARKDOWN_IMAGE_PATTERN = re.compile(
    r"(?P<prefix><img\b[^>]*\bsrc=[\"\'])(?P<scheme>(?:static|work))://(?P<path>[^\"\']+)(?P<suffix>[\"\'])",
    re.IGNORECASE,
)

MARKDOWN_ASSET_TAG_PATTERN = re.compile(
    r"<(?P<tag>img|script|link|audio|video|source|iframe|embed)\b[^>]*>",
    re.IGNORECASE,
)
MARKDOWN_HTTP_ASSET_ATTRIBUTE_PATTERN = re.compile(
    r"\s+(?P<attr>src|href|srcset)=(?P<quote>[\"\'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)

ALLOWED_IMAGE_EXTENSIONS = {
    ".apng",
    ".avif",
    ".gif",
    ".jpg",
    ".jpeg",
    ".png",
    ".svg",
    ".webp",
}


def rewrite_markdown_asset_links(html: str) -> str:
    """Rewrite asset links that reference local asset schemes."""

    def _replace(match: re.Match[str]) -> str:
        scheme = match.group("scheme").lower()
        asset_path = match.group("path").lstrip("/")
        if not asset_path:
            return match.group(0)
        extension = Path(asset_path).suffix.lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            return match.group(0)
        try:
            asset_url = reverse(
                "docs:readme-asset",
                kwargs={"source": scheme, "asset": asset_path},
            )
        except NoReverseMatch:
            return match.group(0)
        return f"{match.group('prefix')}{escape(asset_url)}{match.group('suffix')}"

    return MARKDOWN_IMAGE_PATTERN.sub(_replace, html)


def strip_http_subresources(html: str) -> str:
    """Strip HTTP subresource URLs from HTML output."""

    def _strip_http_attributes(match: re.Match[str]) -> str:
        tag_html = match.group(0)

        def _remove_attr(attr_match: re.Match[str]) -> str:
            if "http://" in attr_match.group("value").lower():
                return ""
            return attr_match.group(0)

        return MARKDOWN_HTTP_ASSET_ATTRIBUTE_PATTERN.sub(_remove_attr, tag_html)

    return MARKDOWN_ASSET_TAG_PATTERN.sub(_strip_http_attributes, html)


def resolve_static_asset(path: str) -> Path:
    normalized = path.lstrip("/")
    if not normalized:
        raise Http404("Asset not found")
    resolved = finders.find(normalized)
    if not resolved:
        raise Http404("Asset not found")
    if isinstance(resolved, (list, tuple)):
        resolved = resolved[0]
    file_path = Path(resolved)
    if file_path.is_dir():
        raise Http404("Asset not found")
    return file_path


def resolve_work_asset(user, path: str) -> Path:
    if not (user and getattr(user, "is_authenticated", False)):
        raise PermissionDenied
    normalized = path.lstrip("/")
    if not normalized:
        raise Http404("Asset not found")
    username = getattr(user, "get_username", None)
    if callable(username):
        username = username()
    else:
        username = getattr(user, "username", "")
    username_component = Path(str(username or user.pk)).name
    base_work = Path(settings.BASE_DIR) / "work"
    try:
        user_dir = Path(safe_join(str(base_work), username_component))
        asset_path = Path(safe_join(str(user_dir), normalized))
    except SuspiciousFileOperation as exc:
        logger.warning("Rejected suspicious work asset path: %s", normalized, exc_info=exc)
        raise Http404("Asset not found") from exc
    try:
        user_dir_resolved = user_dir.resolve(strict=True)
    except FileNotFoundError as exc:
        logger.warning(
            "Work directory missing for asset request: %s", user_dir, exc_info=exc
        )
        raise Http404("Asset not found") from exc
    try:
        asset_resolved = asset_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise Http404("Asset not found") from exc
    try:
        asset_resolved.relative_to(user_dir_resolved)
    except ValueError as exc:
        logger.warning(
            "Rejected work asset outside directory: %s", asset_resolved, exc_info=exc
        )
        raise Http404("Asset not found") from exc
    if asset_resolved.is_dir():
        raise Http404("Asset not found")
    return asset_resolved

