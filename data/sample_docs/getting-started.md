# Getting Started With Acme Storefront

Acme Storefront is a fictional commerce platform used in this demo corpus.
It organizes every merchant account into a tenant, one or more sales channels,
and a set of catalog and order workflows.
The examples in this document are not connected to any live service.

## Core Concepts

A tenant is the top-level container for configuration, users, channels, and API keys.
Tenant identifiers use the form `tenant_key`, such as `demo_fashion`.
Each tenant can have multiple environments so teams can test safely before enabling live traffic.
The platform treats catalog data, order data, and return data as separate domains.

A sales channel represents a storefront, marketplace, kiosk, or integration partner.
Each channel has a `channel_key`, display name, locale defaults, and allowed currencies.
The channel decides which catalog entries are visible to shoppers.
The channel also supplies fulfillment defaults when an order does not provide them directly.

## Sandbox And Production

Acme Storefront has two environment types: sandbox and production.
Sandbox is resettable and is intended for development, demos, and automated tests.
Production is the durable environment used for shopper-facing traffic.
Never copy shopper personal data from production into sandbox.

Sandbox API keys begin with `sandbox_` and production API keys begin with `prod_`.
The same endpoint paths are used in both environments.
The environment is selected by the base URL and the key prefix.
Webhook delivery can be tested in sandbox with replayable events.

## Authentication Model

Server-to-server requests use a bearer token in the `Authorization` header.
The token belongs to one tenant and one environment.
Tokens can be scoped to read-only, order write, refund write, or admin configuration.
The most common scopes are `catalog:read`, `orders:write`, `returns:write`, and `webhooks:read`.

Client applications must not embed server tokens.
For browser flows, create a short-lived session token through the storefront session endpoint.
Session tokens inherit channel restrictions and expire after 30 minutes.
Rotating an API key invalidates newly signed requests immediately.

## Tenant Setup Checklist

Create a tenant record with a stable `tenant_key`.
Add at least one sales channel with a `channel_key`.
Configure supported currencies before importing catalog data.
Set the default tax behavior to either `tax_included` or `tax_excluded`.
Register webhook destinations before testing orders.
Create separate API keys for automation, backend services, and manual support tools.

## Catalog Basics

Catalog items are grouped into products, variants, and price lists.
A product stores shopper-facing copy such as title, description, and media references.
A variant stores sellable attributes such as size, color, SKU, and inventory policy.
Price lists are scoped by channel and currency.
The `sku` field should be stable because it appears in order line items and return records.

## Operational States

Most resources include a `status` field.
Common status values are `draft`, `active`, `archived`, and `disabled`.
An archived product remains visible in historical orders but cannot be added to a new cart.
A disabled channel rejects checkout attempts until it is enabled again.
Status transitions are recorded in the audit trail.

## Webhook Delivery

Webhook endpoints receive signed JSON events.
Each event has an `event_id`, `event_type`, `tenant_key`, and `created_at`.
Consumers should store processed `event_id` values for at least 24 hours to avoid duplicate handling.
If a destination returns a 5xx response, Acme Storefront retries with exponential backoff.
After eight failed attempts, the event is marked `delivery_failed`.

## Local Development Tips

Use sandbox data for local development.
Seed a small catalog with predictable SKUs before testing checkout.
Keep one channel configured for pickup and one channel configured for shipping.
Use webhook replay to verify idempotency.
When a test fails, capture the `request_id` from the response headers and include it in support notes.
