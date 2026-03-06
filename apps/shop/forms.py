from django import forms


class CheckoutForm(forms.Form):
    """Capture shipping and customer data during checkout."""

    customer_name = forms.CharField(max_length=120)
    customer_email = forms.EmailField(max_length=254)
    shipping_address_line1 = forms.CharField(max_length=200)
    shipping_address_line2 = forms.CharField(max_length=200, required=False)
    shipping_city = forms.CharField(max_length=80)
    shipping_postal_code = forms.CharField(max_length=20)
    shipping_country = forms.CharField(max_length=80)


class CartQuantityForm(forms.Form):
    """Validate quantity update payloads for cart interactions."""

    quantity = forms.IntegerField(min_value=0, max_value=999)
