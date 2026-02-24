from apps.blog.models import BlogArticle


def resolve_blog_article_sigils(value: str, *, article: BlogArticle) -> str:
    """Resolve article-specific specialized blog sigils in plain text content."""

    resolved = value or ""
    shortcuts = list(article.sigil_shortcuts.all())
    if not shortcuts:
        return resolved

    for _ in range(10):
        previous = resolved
        for shortcut in shortcuts:
            resolved = resolved.replace(f"[{shortcut.token}]", shortcut.expansion_template)
        if resolved == previous:
            break

    return resolved
