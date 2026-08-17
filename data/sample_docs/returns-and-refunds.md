# Returns And Refunds

Returns and refunds in Acme Storefront are separate but related workflows.
A return tracks the shopper sending items back.
A refund tracks money returned to the shopper.
The workflows can move together, but support teams may inspect them independently.

## Return Eligibility

Most physical items can be returned within 30 days of delivery.
Digital items are not returnable after the download link has been used.
Final-sale products can be excluded from return eligibility by setting `returnable=false` on the variant.
Damaged or incorrect items can be approved even when the standard window has passed.

Eligibility is evaluated against the delivered date, SKU policy, and order status.
Canceled orders do not create return records.
Orders in `created` or `confirmed` status must be canceled instead of returned.
Orders in `released` status can start a return once a fulfillment group is marked delivered.

## Create Return

Use `POST /v1/returns` to create a return request.
The request must include `tenant_key`, `order_id`, and one or more `items`.
Each item includes `sku`, `quantity`, and `reason_code`.
Common `reason_code` values are `too_small`, `too_large`, `damaged`, `wrong_item`, and `changed_mind`.

The response includes `return_id`, `return_number`, `status`, and item-level eligibility results.
The initial status is `requested`.
If all items are eligible, the return can move to `approved`.
If any item is ineligible, the response lists the rejected item and the policy reason.

## Return Statuses

| Status | Meaning |
| --- | --- |
| `requested` | Shopper or support created the request. |
| `approved` | Items are eligible and a label can be issued. |
| `in_transit` | Carrier scan shows the package is moving. |
| `received` | Warehouse received the package. |
| `inspected` | Warehouse completed item inspection. |
| `closed` | Return workflow is finished. |

Support tools should display the latest status and the last status change timestamp.
Warehouse systems should update `received` and `inspected` statuses rather than editing refund records directly.

## Refund Policy

Refunds are issued after returned items are inspected unless the tenant enables instant refunds.
The default refund method is the original payment method.
Shipping fees are refunded only when the reason code is `damaged` or `wrong_item`.
Gift wrap fees are never refunded.
Partial refunds are allowed when only some items pass inspection.

Refund records include `refund_id`, `return_id`, `order_id`, `amount`, `currency`, and `status`.
Refund statuses are `pending`, `submitted`, `succeeded`, and `failed`.
If a refund fails, support can retry after correcting payment metadata.

## Refund API

Use `POST /v1/refunds` to submit a refund.
This refund endpoint submits approved refund amounts after return inspection.
The request must include `tenant_key`, `return_id`, `amount`, and `currency`.
The API rejects amounts greater than the refundable balance.
Use `GET /v1/refunds/{refund_id}` to check the final state.
Each response includes a `request_id` for support diagnostics.

## Webhook Events

Return workflow changes emit `return.created`, `return.approved`, `return.received`, and `return.closed`.
Refund workflow changes emit `refund.submitted`, `refund.succeeded`, and `refund.failed`.
Webhook payloads include `tenant_key`, `order_id`, `return_id`, and the current status.
Consumers should dedupe events by `event_id`.
When a refund webhook arrives before a return webhook, re-read the return before updating a dashboard.

## Support Playbook

When a shopper asks about a return, search by `order_id` or `return_number`.
Confirm the item-level eligibility result before promising a refund.
If the item is marked final sale, explain the policy and check whether a damage exception applies.
If a refund is delayed, inspect the refund `status` and the last payment processor message.
Do not ask shoppers to send payment details through support notes.

## Reporting

The returns report groups counts by reason code, SKU, channel, and week.
Refund reporting separates gross refunded amount from shipping-fee refunds.
Finance teams should reconcile succeeded refunds against settlement records.
Operations teams should watch the `damaged` reason code for product quality trends.
