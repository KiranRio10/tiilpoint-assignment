# NOTES

Fill in every field below before you submit. We read this file first: a submission with an empty field is not graded.
Write in your own words. If you did not fix something, set Status to `not fixed` and say what you found and tried; that still counts.
Status is one of: `fixed`, `partly fixed`, `not fixed`. Commits: the short hashes of the commits for this item, or `none`.

## 1. Report

> Finance says that on some days the revenue in the daily report is higher than what the payment provider paid out, and a few orders show more money received than their total.

**Status:** fixed

**What I saw:** When I sent the same payment webhook event twice (simulating a provider retry), two separate payment rows were inserted with the same `event_id`. The `paid_cents()` sum doubled, and the daily report's revenue was inflated accordingly. An order with a 1200 cent total showed 2400 cents received after one retry.

**Root cause:** `payments.py handle_event()` had no idempotency check on `event_id`. The README says "The payment provider retries a webhook until it receives a 2xx response." Every retry inserted a duplicate payment row, so `SUM(amount_cents)` grew with each retry, inflating both the per-order paid total and the daily revenue report.

**Fix and why:** Added a `SELECT 1 FROM payments WHERE event_id = ?` check before the INSERT. If the event_id already exists, we return the current order status without inserting again. This is the standard idempotency pattern for webhook handlers — the event_id is the natural deduplication key provided by the payment provider.

**How I verified it:** Wrote `test_duplicate_webhook_does_not_double_revenue` — sends the same event_id three times and asserts `paid_cents` equals the order total (not tripled). Also wrote `test_duplicate_webhook_does_not_inflate_daily_revenue` — confirms the daily report shows correct revenue after retries. Both tests fail on the original code and pass on the fix.

**Commits:** 55c502c

## 2. Report

> Products show as sold out, or nearly, while the shelf is full. Staff say it gets worse on days when many customers abandon checkout.

**Status:** fixed

**What I saw:** Created a pending order (not yet paid), then cancelled it. The stock was not restored. After 5 customers each ordered 1 unit and all abandoned (cancelled), stock dropped from 5 to 0 and stayed there permanently.

**Root cause:** In `orders.py cancel()`, stock was only released when `order["status"] == "paid"`. But stock is reserved at order *creation* time (in `create()` via `catalog.reserve()`), regardless of payment status. Pending orders that are cancelled never had their reserved stock returned, causing a permanent stock leak proportional to the number of abandoned checkouts.

**Fix and why:** Changed the condition from `if order["status"] == "paid"` to `if order["status"] in ("pending", "paid")`. Both statuses have stock reserved, so both need it released on cancel. This is the minimal fix — it matches the symmetry of `create()` which reserves stock before payment.

**How I verified it:** Wrote `test_cancel_pending_order_restocks` — creates a pending order for 3 units, confirms stock drops to 2, cancels, and asserts stock returns to 5. Also wrote `test_multiple_abandoned_checkouts_do_not_drain_stock` — 5 orders of 1 unit each, all cancelled, stock returns to 5. Both fail on original code, pass on fix.

**Commits:** a9c292c

## 3. Report

> Customers in taxed regions who used a coupon say the tax on their receipt is too high. Receipts without a coupon are right.

**Status:** fixed

**What I saw:** An order for 2 mugs at 1000 cents each in CA (7.25% tax) with a 10% coupon showed tax of 145 cents (7.25% of 2000), but it should be 131 cents (7.25% of 1800, the post-discount amount). Without a coupon, tax was correct.

**Root cause:** In `pricing.py price_order()`, the tax was computed on the full subtotal *before* the discount was calculated: `tax = tax_for(sub, region)` appeared before `discount = discount_for(coupon, sub)`. The discount reduced the total but not the tax base, so customers with coupons were overtaxed. Sales tax should be charged on the net amount the customer actually pays (subtotal minus discount).

**Fix and why:** Swapped the order: compute discount first, then compute tax on `(sub - discount)`. This follows standard sales tax rules where tax applies to the actual sale price after discounts. The change is two lines in `price_order()`.

**How I verified it:** Wrote `test_tax_computed_after_percent_discount` — verifies tax is calculated on 1800 (2000 - 200) not 2000, and is strictly less than tax on the full subtotal. Also `test_tax_computed_after_fixed_discount` for fixed coupons, and `test_no_coupon_tax_unchanged` to confirm no regression without coupons. All fail on original, pass on fix.

**Commits:** 3ac6f2a

## 4. Report

> Our shop in India (UTC+5:30) says sales taken in the morning do not appear in that day's report and turn up under the day before.

**Status:** fixed

**What I saw:** A payment at 09:00 IST on 2025-03-10 (which is 03:30 UTC) did not appear in the report for 2025-03-10 with `utc_offset_minutes=330`. It appeared under 2025-03-09 instead. The day bounds were computed as 05:30 UTC to next-day 05:30 UTC instead of 18:30 UTC (prev day) to 18:30 UTC.

**Root cause:** In `reports.py day_bounds_utc()`, the UTC offset was *added* to midnight UTC instead of *subtracted*. To convert a local midnight to UTC, you subtract the offset. India is UTC+5:30 (+330 min), so local midnight (00:00 IST on March 10) = 2025-03-09T18:30:00Z. The code did `start += timedelta(minutes=330)` giving 05:30 UTC, which is 11:00 IST — wrong direction entirely. Morning sales (before 11:00 IST) fell into the previous day's window.

**Fix and why:** Changed `+=` to `-=` in the offset application: `start -= timedelta(minutes=utc_offset_minutes)`. This correctly converts local midnight to UTC for any positive offset timezone.

**How I verified it:** Wrote `test_day_bounds_utc_india` — directly checks that bounds for 2025-03-10 with +330 offset are 2025-03-09T18:30:00Z to 2025-03-10T18:30:00Z. Also `test_india_morning_sale_in_correct_day` (payment at 03:30 UTC appears in March 10 India report) and `test_india_morning_not_in_previous_day` (same payment does NOT appear in March 9). All three fail on original, pass on fix.

**Commits:** ff3b5e2

## 5. Report

> Ever since we started running two server workers, we have sold more units of an item than we had, and its stock went negative.

**Status:** fixed

**What I saw:** With two threads hitting the same SQLite database, both could successfully reserve the last unit of a product, pushing stock to -1. The check-then-update pattern in `reserve()` has a classic TOCTOU (time-of-check/time-of-use) race.

**Root cause:** `catalog.py reserve()` did a two-step operation: (1) read the product and check `if product.stock < qty`, then (2) `UPDATE products SET stock = stock - ?`. With two workers sharing the same database, both could read stock=1, both pass the check, and both execute the UPDATE, resulting in stock = 1 - 1 - 1 = -1. The `BEGIN IMMEDIATE` in `orders.create()` helps with SQLite's write lock, but the cache read in `get_product()` could bypass it since the check used a cached value.

**Fix and why:** Replaced the check-then-update with a single atomic SQL statement: `UPDATE products SET stock = stock - ? WHERE sku = ? AND stock >= ?`. If `rowcount == 0`, either the product doesn't exist (we call `get_product()` which raises `UnknownProduct`) or stock is insufficient (we raise `OutOfStock` with current stock). The atomicity of the SQL UPDATE ensures only one worker can claim the last unit.

**How I verified it:** Wrote `test_atomic_reserve_prevents_negative_stock` — uses two threads to concurrently reserve the last unit of a product with stock=1 from a file-based SQLite DB. Asserts that at most one succeeds and stock never goes negative. Also `test_reserve_still_raises_out_of_stock` to confirm the basic OutOfStock behavior is preserved.

**Commits:** 02d84eb

## 6. Open question

> Open question from support, no single right answer: sometimes a customer really does pay twice for one order (two separate payments, not a retry of the same one). Today the system records both and says nothing. Decide what should happen, build the smallest version of your decision, and explain what it costs in `NOTES.md`.

**My decision:** Record both payments (for audit trail), but flag the order as `"overpaid"` when total payments exceed the order total. Return `"overpaid"` as the status so that finance/support dashboards can filter for overpaid orders and issue manual refunds.

**Why:** This is the safest approach. We don't want to silently reject the second payment (the money has already been charged by the payment provider). We don't want to auto-refund (that requires calling the payment provider's refund API, introducing complexity, failure modes, and partial-refund edge cases). By recording and flagging, we preserve the full audit trail and let humans decide how to handle each case — some may be legitimate split payments, some may be errors.

**What it costs:** (1) A new order status value `"overpaid"` that any code checking `order["status"]` must now handle (e.g., the `ship()` method currently only ships `"paid"` orders — an overpaid order won't ship until a human resolves it, which is arguably correct). (2) Finance/support needs a way to query overpaid orders and trigger refunds manually — this is outside the current system but the flag makes it possible. (3) If the team later wants auto-refunds, the status can serve as the trigger; we're not blocking that path.

**What I built:** In `payments.py handle_event()`, after inserting a new (non-duplicate) payment, check if `paid > total_cents` and the order is already `"paid"` or `"shipped"`. If so, UPDATE status to `"overpaid"` and return `"overpaid"`. The idempotency check (bug 1 fix) ensures this only triggers for genuinely distinct payments, not webhook retries.

**How I verified it:** Wrote `test_double_payment_marks_order_overpaid` — pays an order fully, then sends a second distinct payment event, asserts status becomes `"overpaid"` and both payments are recorded (paid_cents = 2400 for a 1200 order). Also `test_partial_then_full_no_overpaid` — two partial payments that sum to exactly the total result in `"paid"`, not `"overpaid"`.

**Commits:** 55c502c (included in the payments fix commit alongside bug 1)

## Other things I noticed

1. **Unclosed database connections:** The test suite produces `ResourceWarning: unclosed database` warnings. Each `db.connect()` returns a connection that is never explicitly closed. In production, adding a `close()` method or using context managers would be better practice. Not fixed — cosmetic warning only.

2. **`isolation_level=None` in db.connect():** The connection uses autocommit mode (`isolation_level=None`), which means every statement outside an explicit `BEGIN/COMMIT` block is auto-committed immediately. This is intentional for the manual transaction management in `orders.py`, but it means any code that forgets `BEGIN IMMEDIATE` before a multi-statement operation has no implicit transaction safety. Worth documenting.

3. **Catalog read cache never invalidated by other connections:** The `_cache` dict in `Catalog` is per-process and never sees changes from other workers. With two workers, one could cache a stale price or stock value. The atomic `reserve()` fix (bug 5) mitigates the stock issue, but stale prices could still cause order totals to be calculated with an outdated unit price. Not fixed — would need cache invalidation or removal of the cache.

## Session logs

Tool used: Google Antigravity (AI coding assistant)

- `session_logs/antigravity_session.md` — full session export
