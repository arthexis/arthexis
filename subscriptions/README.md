# Subscriptions App

Provides a simple subscription model:

- `GET /subscriptions/products/` returns available products.
- `POST /subscriptions/subscribe/` with `user_id` and `product_id` creates a subscription.
- `GET /subscriptions/list/?user_id=<id>` lists subscriptions for a user.
