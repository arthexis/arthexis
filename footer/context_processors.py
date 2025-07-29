from .utils import get_footer_columns


def footer_links(request):
    return {"footer_columns": get_footer_columns()}
