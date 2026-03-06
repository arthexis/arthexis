"""Forms used by the shop storefront checkout and tracking pages."""

from django import forms

from apps.shop.models import ShopOrder


class AddToCartForm(forms.Form):
    """Capture quantity updates for a cart line."""

    quantity = forms.IntegerField(min_value=1, initial=1)


class CheckoutForm(forms.ModelForm):
    """Collect checkout information needed to create an order."""

    class Meta:
        model = ShopOrder
        fields = [
            "customer_name",
            "customer_email",
            "payment_provider",
            "shipping_address_line1",
            "shipping_address_line2",
            "shipping_city",
            "shipping_postal_code",
            "shipping_country",
        ]


class OrderTrackingForm(forms.Form):
    """Find an order by order number and customer email."""

    order_id = forms.IntegerField(min_value=1)
    customer_email = forms.EmailField()
