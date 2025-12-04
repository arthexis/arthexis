# Payment Processors Cookbook

Configure OpenPay, PayPal, and Stripe credentials for charging workflows.

- [Access points](#access-points)
- [OpenPay setup](#openpay-setup)
- [PayPal setup](#paypal-setup)
- [Stripe setup](#stripe-setup)
- [Webhook signing](#webhook-signing)

## Access points

- Manage processor credentials in the Django admin at **Payment Processors** (`/admin/payments/openpayprocessor/`, `/admin/payments/paypalprocessor/`, `/admin/payments/stripeprocessor/`).
- Each processor belongs to a specific user or security group. Use the owner fields at the top of the form to scope credentials.
- Use the **Verify credentials** action on each change form to validate API access without processing real charges.

## OpenPay setup

1. Collect the **merchant ID**, **public key**, **private key**, and **webhook secret** from your OpenPay dashboard.
2. Disable **Production** while validating in the sandbox; enable it when ready for live charges.
3. Save the profile and click **Verify credentials** to confirm access to the OpenPay API.

## PayPal setup

1. Create REST API credentials in PayPal and copy the **client ID** and **client secret**.
2. Provide the **webhook ID** PayPal assigns to your notification endpoint so events can be validated.
3. Toggle **Production** to target live PayPal instead of the sandbox.
4. Save and run **Verify credentials** to ensure the REST token endpoint accepts your keys.

## Stripe setup

1. From the Stripe dashboard, create or retrieve a **secret key** and **publishable key** for your environment.
2. Paste the keys into the **Stripe** section and choose **Stripe Live** when using production credentials; leave it off for test keys.
3. Add the **webhook secret** from your Stripe endpoint so signatures can be validated.
4. Save and select **Verify credentials** to confirm the account can be read via the Stripe API.

## Webhook signing

- Store the webhook signing secrets for each processor to validate inbound notifications.
- Regenerate webhook secrets in your payment provider when rotating credentials and update the corresponding fields here.
- Run the admin verification action after changes to clear cached verification state and confirm connectivity.
