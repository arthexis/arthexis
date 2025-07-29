# Subscriptions App

Provides a simple subscription model:

- `GET /subscriptions/products/` returns available products.
- `POST /subscriptions/subscribe/` with `account_id` and `product_id` creates a subscription.
- `GET /subscriptions/list/?account_id=<id>` lists subscriptions for an account.
