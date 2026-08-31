from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.core.exceptions import ValidationError
from django.utils import timezone


class OdooQuoteReportError(Exception):
    """Raised when Odoo quote report data cannot be assembled."""


@dataclass(frozen=True)
class OdooQuoteReportParams:
    """Validated non-HTTP parameters for building an Odoo quote report.

    Parameters:
        quote_window_days: Number of days of quote history to query.
        recent_product_limit: Maximum number of recently updated products to fetch.

    Returns:
        OdooQuoteReportParams: Immutable validated report parameters.

    Raises:
        ValidationError: Raised when either value falls outside the accepted range.
    """

    quote_window_days: int = 90
    recent_product_limit: int = 10

    @classmethod
    def from_request(cls, request) -> "OdooQuoteReportParams":
        """Build validated report parameters from a Django request.

        Parameters:
            request: Django request carrying optional query-string overrides.

        Returns:
            OdooQuoteReportParams: Parsed and validated parameters.

        Raises:
            ValidationError: Raised when a provided query-string value is invalid.
        """

        return cls(
            quote_window_days=_parse_positive_int(
                request.GET.get("quote_window_days"),
                field_name="quote_window_days",
                default=90,
                minimum=1,
                maximum=365,
            ),
            recent_product_limit=_parse_positive_int(
                request.GET.get("recent_product_limit"),
                field_name="recent_product_limit",
                default=10,
                minimum=1,
                maximum=100,
            ),
        )


@dataclass(frozen=True)
class OdooQuoteReportData:
    """Raw Odoo quote report data assembled for later presentation.

    Parameters:
        template_stats: Quote template usage rows.
        quotes: Raw quote records with related metadata expanded.
        recent_products: Recently updated product rows.
        installed_modules: Installed module rows.

    Returns:
        OdooQuoteReportData: Structured raw report payload.
    """

    template_stats: list[dict[str, object]]
    quotes: list[dict[str, object]]
    recent_products: list[dict[str, object]]
    installed_modules: list[dict[str, object]]


def _parse_positive_int(
    value: str | None,
    *,
    field_name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Parse an optional positive integer bounded to a configured range.

    Parameters:
        value: Incoming string value, or ``None`` to use the default.
        field_name: Validation field name used in raised errors.
        default: Value to return when ``value`` is blank.
        minimum: Smallest accepted integer value.
        maximum: Largest accepted integer value.

    Returns:
        int: Parsed integer value.

    Raises:
        ValidationError: Raised when the value is not an integer or falls outside the range.
    """

    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Enter a whole number."}) from exc
    if parsed < minimum or parsed > maximum:
        raise ValidationError(
            {field_name: f"Ensure this value is between {minimum} and {maximum}."}
        )
    return parsed


def assemble_odoo_quote_report_data(profile, *, params: OdooQuoteReportParams) -> OdooQuoteReportData:
    """Fetch and normalize raw Odoo data needed for the quote report.

    Parameters:
        profile: Verified ``OdooEmployee`` profile used for RPC calls.
        params: Validated report parameters.

    Returns:
        OdooQuoteReportData: Structured raw report data for presentation.

    Raises:
        OdooQuoteReportError: Raised when any Odoo RPC call fails.
    """

    try:
        templates = profile.execute(
            "sale.order.template",
            "search_read",
            fields=["name"],
            order="name asc",
        )
        template_usage = profile.execute(
            "sale.order",
            "read_group",
            [[("sale_order_template_id", "!=", False)]],
            ["sale_order_template_id"],
            lazy=False,
        )

        usage_map: dict[int, int] = {}
        for entry in template_usage:
            template_info = entry.get("sale_order_template_id")
            if not template_info:
                continue
            usage_map[template_info[0]] = entry.get("sale_order_template_id_count", 0)

        raw_quotes = _fetch_quotes(profile, params=params)
        raw_products = profile.execute(
            "product.product",
            "search_read",
            fields=["name", "default_code", "write_date", "create_date"],
            limit=params.recent_product_limit,
            order="write_date desc, create_date desc",
        )
        modules = profile.execute(
            "ir.module.module",
            "search_read",
            [[("state", "=", "installed")]],
            fields=["name", "shortdesc", "latest_version", "author"],
            order="name asc",
        )
    except Exception as exc:
        raise OdooQuoteReportError("Unable to fetch quote report data from Odoo.") from exc

    return OdooQuoteReportData(
        template_stats=[
            {
                "id": template.get("id"),
                "name": template.get("name", ""),
                "quote_count": usage_map.get(template.get("id"), 0),
            }
            for template in templates
        ],
        quotes=raw_quotes,
        recent_products=list(raw_products),
        installed_modules=[
            {
                "name": module.get("name", ""),
                "shortdesc": module.get("shortdesc", ""),
                "latest_version": module.get("latest_version", ""),
                "author": module.get("author", ""),
            }
            for module in modules
        ],
    )


def _fetch_quotes(profile, *, params: OdooQuoteReportParams) -> list[dict[str, object]]:
    """Fetch quote rows and expand related tag and currency metadata.

    Parameters:
        profile: Verified ``OdooEmployee`` profile used for RPC calls.
        params: Validated report parameters.

    Returns:
        list[dict[str, object]]: Raw quote rows enriched with tags and currency details.
    """

    quote_window_start = timezone.now() - timedelta(days=params.quote_window_days)
    quotes = profile.execute(
        "sale.order",
        "search_read",
        [
            [
                ("create_date", ">=", quote_window_start.strftime("%Y-%m-%d %H:%M:%S")),
                ("state", "!=", "cancel"),
                ("quote_sent", "=", False),
            ]
        ],
        fields=[
            "name",
            "amount_total",
            "partner_id",
            "activity_type_id",
            "activity_summary",
            "tag_ids",
            "create_date",
            "currency_id",
        ],
        order="create_date desc",
    )

    tag_ids: set[int] = set()
    currency_ids: set[int] = set()
    for quote in quotes:
        tag_ids.update(quote.get("tag_ids") or [])
        currency_info = quote.get("currency_id")
        if isinstance(currency_info, (list, tuple)) and currency_info and currency_info[0]:
            currency_ids.add(currency_info[0])

    tag_map: dict[int, str] = {}
    if tag_ids:
        for tag in profile.execute("sale.order.tag", "read", list(tag_ids), fields=["name"]):
            tag_id = tag.get("id")
            if tag_id is not None:
                tag_map[tag_id] = tag.get("name", "")

    currency_map: dict[int, dict[str, str]] = {}
    if currency_ids:
        for currency in profile.execute(
            "res.currency", "read", list(currency_ids), fields=["name", "symbol"]
        ):
            currency_id = currency.get("id")
            if currency_id is not None:
                currency_map[currency_id] = {
                    "name": currency.get("name", ""),
                    "symbol": currency.get("symbol", ""),
                }

    return [
        {
            "name": quote.get("name", ""),
            "partner_id": quote.get("partner_id"),
            "activity_type_id": quote.get("activity_type_id"),
            "activity_summary": quote.get("activity_summary") or "",
            "tag_names": [tag_map.get(tag_id, str(tag_id)) for tag_id in quote.get("tag_ids") or []],
            "create_date": quote.get("create_date"),
            "amount_total": quote.get("amount_total") or 0,
            "currency": _resolve_currency_details(quote.get("currency_id"), currency_map),
        }
        for quote in quotes
    ]


def _resolve_currency_details(currency_info, currency_map: dict[int, dict[str, str]]) -> dict[str, str]:
    """Resolve a quote currency record into a compact display dictionary.

    Parameters:
        currency_info: Currency tuple returned by Odoo.
        currency_map: Expanded currency records keyed by Odoo ID.

    Returns:
        dict[str, str]: Currency name and symbol suitable for later formatting.
    """

    if not isinstance(currency_info, (list, tuple)) or not currency_info:
        return {"name": "", "symbol": "", "label": ""}
    currency_id = currency_info[0]
    currency_details = currency_map.get(currency_id, {})
    label = (
        currency_details.get("symbol")
        or currency_details.get("name")
        or (currency_info[1] if len(currency_info) >= 2 else "")
    )
    return {
        "name": currency_details.get("name", ""),
        "symbol": currency_details.get("symbol", ""),
        "label": label,
    }


def build_odoo_quote_report_context_data(report_data: OdooQuoteReportData) -> dict[str, list[dict[str, object]]]:
    """Convert raw report data into template-friendly presentation rows.

    Parameters:
        report_data: Structured raw report data returned by the Odoo service layer.

    Returns:
        dict[str, list[dict[str, object]]]: Template-ready collections for rendering.
    """

    return {
        "template_stats": report_data.template_stats,
        "quotes": [_present_quote(quote) for quote in report_data.quotes],
        "recent_products": [_present_product(product) for product in report_data.recent_products],
        "installed_modules": report_data.installed_modules,
    }


def _present_quote(quote: dict[str, object]) -> dict[str, object]:
    """Transform a raw quote row into a template-friendly presentation row.

    Parameters:
        quote: Raw quote row with related metadata already expanded.

    Returns:
        dict[str, object]: Presented quote row.
    """

    partner = quote.get("partner_id")
    customer = partner[1] if isinstance(partner, (list, tuple)) and len(partner) >= 2 else ""
    activity_type = quote.get("activity_type_id")
    activity_name = (
        activity_type[1]
        if isinstance(activity_type, (list, tuple)) and len(activity_type) >= 2
        else ""
    )
    activity_summary = quote.get("activity_summary") or ""
    activity_value = activity_summary or activity_name
    amount_total = quote.get("amount_total") or 0
    currency = quote.get("currency")
    currency_label = ""
    if isinstance(currency, dict):
        currency_label = str(currency.get("label") or "")
    total_display = f"{currency_label}{amount_total:,.2f}"
    return {
        "name": quote.get("name", ""),
        "customer": customer,
        "activity": activity_value,
        "tags": quote.get("tag_names") or [],
        "create_date": parse_odoo_datetime(quote.get("create_date")),
        "total": amount_total,
        "total_display": total_display,
    }


def _present_product(product: dict[str, object]) -> dict[str, object]:
    """Transform a raw product row into a template-friendly presentation row.

    Parameters:
        product: Raw product row returned from Odoo.

    Returns:
        dict[str, object]: Presented product row.
    """

    return {
        "name": product.get("name", ""),
        "default_code": product.get("default_code", ""),
        "create_date": parse_odoo_datetime(product.get("create_date")),
        "write_date": parse_odoo_datetime(product.get("write_date")),
    }


def parse_odoo_datetime(value) -> datetime | None:
    """Parse Odoo date/datetime values into aware datetimes.

    Parameters:
        value: Odoo datetime value or string.

    Returns:
        datetime | None: Timezone-aware datetime when parsing succeeds, else ``None``.
    """

    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value)
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            text_iso = text.replace(" ", "T")
            try:
                dt = datetime.fromisoformat(text_iso)
            except ValueError:
                dt = None
                for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                    try:
                        dt = datetime.strptime(text, fmt)
                        break
                    except ValueError:
                        continue
                if dt is None:
                    return None
    assert dt is not None
    if timezone.is_naive(dt):
        tzinfo = getattr(timezone, "utc", datetime_timezone.utc)
        dt = timezone.make_aware(dt, tzinfo)
    return dt
