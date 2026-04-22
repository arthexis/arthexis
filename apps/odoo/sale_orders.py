from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import OdooSaleFactor, OdooSaleOrderTemplate


@dataclass(frozen=True)
class CreatedSaleOrder:
    order_id: int
    customer_id: int
    order_url: str


class OdooSaleOrderBuilder:
    """Build and create sale orders from an Odoo template and configured factors."""

    def __init__(self, *, profile):
        self.profile = profile

    def _template_lines(self, template_id: int) -> list[dict[str, object]]:
        rows = self.profile.execute(
            "sale.order.template.line",
            "search_read",
            [[("sale_order_template_id", "=", template_id)]],
            {
                "fields": [
                    "name",
                    "product_id",
                    "price_unit",
                    "product_uom_qty",
                ],
            },
        )
        return list(rows or [])

    def _note_value(self, template: OdooSaleOrderTemplate) -> str:
        if not template.note_template:
            return ""
        if template.resolve_note_sigils:
            return template.resolve_sigils("note_template")
        return template.note_template

    def _factor_lines(
        self,
        *,
        template: OdooSaleOrderTemplate,
        factor_values: dict[str, Decimal],
    ) -> list[tuple[int, int, dict[str, object]]]:
        lines: list[tuple[int, int, dict[str, object]]] = []
        factors = OdooSaleFactor.objects.prefetch_related("templates", "product_rules")
        for factor in factors:
            value = Decimal(str(factor_values.get(factor.code, 0) or 0))
            if value <= 0 or not factor.applies_to_template(template):
                continue
            for rule in factor.product_rules.all():
                qty = rule.quantity_for_factor_value(value)
                if qty <= 0:
                    continue
                product_id = rule.product_id()
                if product_id is None:
                    raise ValidationError(
                        f"Factor rule '{rule.name}' has an invalid Odoo product identifier."
                    )
                lines.append(
                    (
                        0,
                        0,
                        {
                            "product_id": product_id,
                            "product_uom_qty": float(qty),
                            "name": rule.name,
                        },
                    )
                )
        return lines

    def create_order(
        self,
        *,
        template: OdooSaleOrderTemplate,
        customer_name: str,
        customer_email: str,
        factor_values: dict[str, Decimal] | None = None,
        partner_language: str | None = None,
    ) -> CreatedSaleOrder:
        factor_values = factor_values or {}
        template_id = template.template_id()
        if template_id is None:
            raise ValidationError("Template is missing a valid Odoo template id.")

        order_lines: list[tuple[int, int, dict[str, object]]] = []
        for line in self._template_lines(template_id):
            product = line.get("product_id")
            if not isinstance(product, (list, tuple)) or not product:
                continue
            order_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": int(product[0]),
                        "product_uom_qty": float(line.get("product_uom_qty") or 1),
                        "name": line.get("name") or "",
                        "price_unit": float(line.get("price_unit") or 0),
                    },
                )
            )

        order_lines.extend(
            self._factor_lines(template=template, factor_values=factor_values)
        )

        language = (
            partner_language
            or template.default_new_customer_language
            or template.fallback_new_customer_language
            or "en_US"
        )

        partner_id = self.profile.execute(
            "res.partner",
            "create",
            [
                {
                    "name": customer_name,
                    "email": customer_email,
                    "lang": language,
                }
            ],
        )
        salesperson_uid = getattr(template.salesperson, "odoo_uid", None)
        order_values = {
            "partner_id": partner_id,
            "sale_order_template_id": template_id,
            "order_line": order_lines,
            "note": self._note_value(template),
        }
        if salesperson_uid:
            order_values["user_id"] = salesperson_uid

        order_id = self.profile.execute("sale.order", "create", [order_values])
        host = str(self.profile.host).rstrip("/")
        return CreatedSaleOrder(
            order_id=order_id,
            customer_id=partner_id,
            order_url=f"{host}/web#id={order_id}&model=sale.order&view_type=form",
        )
