# Orders API

The Orders API creates and reads orders for Acme Storefront tenants.
It is designed for backend integrations that already validated the cart and shopper session.
All examples are fictional and use the versioned path prefix `/v1`.

## Create Order

Use `POST /v1/orders` to create an order.
The request must include `tenant_key`, `channel_key`, `currency`, `line_items`, and `fulfillment_type`.
The supported `fulfillment_type` values are `ship_to_address`, `store_pickup`, and `digital_delivery`.
The response returns `order_id`, `order_number`, `status`, and the accepted line items.

Orders start in the `created` status.
Payment authorization can move an order to `confirmed`.
Fulfillment export can move an order to `released`.
Canceled orders use the `canceled` status and keep their original order number for audit purposes.

## Request Body Fields

| Field | Required | Notes |
| --- | --- | --- |
| `tenant_key` | yes | Tenant that owns the order. |
| `channel_key` | yes | Sales channel receiving the order. |
| `currency` | yes | ISO-style currency code used by the channel. |
| `line_items` | yes | Array of SKU, quantity, and unit price records. |
| `fulfillment_type` | yes | One of `ship_to_address`, `store_pickup`, or `digital_delivery`. |
| `external_reference` | no | Idempotency-friendly caller reference. |
| `customer_email` | no | Optional contact address for receipts. |

The `external_reference` field should be unique per tenant.
If a caller sends the same `external_reference` with an identical body, the API returns the existing order.
If the body differs, the API returns `ACME_ORDER_CONFLICT`.

## Read Order

Use `GET /v1/orders/{order_id}` to fetch a single order.
The response includes totals, line items, fulfillment groups, payments, and return summaries.
Use `GET /v1/orders?external_reference=...` when the caller only knows its own reference.
List responses are sorted by `created_at` descending.

## Fulfillment Details

Shipping orders require a `shipping_address` object with recipient name, street, city, region, postal code, and country.
Pickup orders require a `pickup_location_id`.
Digital orders require a `delivery_email` unless `customer_email` is already present.
The API rejects mixed fulfillment types in a single order.
Split shipments are represented after creation as separate fulfillment groups.

## Error Codes

| Code | Meaning | Recommended action |
| --- | --- | --- |
| `ACME_ORDER_INVALID` | Required field missing or malformed. | Fix the request body and retry. |
| `ACME_ORDER_CONFLICT` | Duplicate `external_reference` with a different body. | Reconcile caller state before retrying. |
| `ACME_ORDER_SKU_NOT_FOUND` | A line item SKU is not active for the channel. | Verify catalog publication. |
| `ACME_ORDER_CURRENCY_UNSUPPORTED` | Currency is not configured for the channel. | Update channel currency settings. |
| `ACME_ORDER_FULFILLMENT_UNSUPPORTED` | Fulfillment type is not enabled for the channel. | Enable the mode or choose another channel. |

Every error response includes `request_id`, `code`, `message`, and optional `details`.
The `request_id` is safe to share in support tickets because it does not contain shopper data.

## Idempotency

Acme Storefront supports idempotent create calls through `external_reference`.
Callers should generate a stable reference before the first order attempt.
Do not use a timestamp alone as the reference because retries would create different values.
If network failure occurs after submission, retry with the same `external_reference`.
The API keeps idempotency records for 48 hours.

## Rate Limits

The Orders API applies tenant-level rate limits.
The default limit is 600 create attempts per minute and 1,200 read attempts per minute.
Responses include `rate_limit_remaining` and `rate_limit_reset_at`.
When the API returns HTTP 429, wait until `rate_limit_reset_at` before retrying.
Bulk historical exports should use the reporting feed instead of the read endpoint.

## Webhook Events

Order changes emit webhook events.
The main event types are `order.created`, `order.confirmed`, `order.released`, and `order.canceled`.
Each event includes `order_id`, `order_number`, `tenant_key`, `channel_key`, and `status`.
Consumers should treat events as at-least-once delivery.
Use the Orders API to re-read the current state when event order matters.
