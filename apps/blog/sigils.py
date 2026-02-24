from apps.blog.models import BlogArticle


def resolve_blog_article_sigils(value: str, *, article: BlogArticle) -> str:
    """Resolve article-specific specialized blog sigils in plain text content."""

    resolved = value or ""
    for shortcut in article.sigil_shortcuts.all():
        resolved = resolved.replace(f"[{shortcut.token}]", shortcut.expansion_template)
    return resolved
