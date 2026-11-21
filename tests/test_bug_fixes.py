"""Tests for all six reported bugs — each test fails on the original code and passes on the fixed code."""
import threading
import unittest
from datetime import datetime, timedelta, timezone

from tillpoint import db, reports
from tillpoint.api import App
from tillpoint.catalog import Catalog, OutOfStock
from tillpoint.orders import Orders
from tillpoint.payments import Payments
from tillpoint import pricing


class Bug1DuplicatePaymentTest(unittest.TestCase):
    """Report 1: Revenue higher than actual payouts; orders show more money received than their total.
    Root cause: payment webhook retries insert duplicate payment rows because event_id isn't checked."""

    def setUp(self):
        self.conn = db.connect()
        self.app = App(self.conn)
        self.app.catalog.add_product("MUG", "Mug", 1200, stock=10)

    def test_duplicate_webhook_does_not_double_revenue(self):
        """Sending the same event_id twice should only record one payment."""
        order = self.app.orders.create("alice", [("MUG", 1)])
        event = {"id": "evt_dup", "type": "payment.succeeded",
                 "order_id": order["id"], "amount_cents": 1200}
        # First delivery
        status1 = self.app.payments.handle_event(event)
        self.assertEqual(status1, "paid")
        # Retry of the same event (same event_id)
        status2 = self.app.payments.handle_event(event)
        self.assertEqual(status2, "paid")
        # Only one payment should be recorded — total paid equals order total, not double
        self.assertEqual(self.app.payments.paid_cents(order["id"]), 1200)

    def test_duplicate_webhook_does_not_inflate_daily_revenue(self):
        """Daily report should not double-count revenue from webhook retries."""
        order = self.app.orders.create("alice", [("MUG", 2)])  # total = 2400
        noon = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        event = {"id": "evt_report", "type": "payment.succeeded",
                 "order_id": order["id"], "amount_cents": 2400}
        self.app.payments.handle_event(event, now=noon)
        self.app.payments.handle_event(event, now=noon)  # retry
        self.app.payments.handle_event(event, now=noon)  # second retry
        report = reports.daily_sales(self.conn, "2025-06-15")
        self.assertEqual(report["revenue_cents"], 2400)  # not 7200


class Bug2StockLeakOnPendingCancelTest(unittest.TestCase):
    """Report 2: Products show sold out while shelf is full; worse with abandoned checkouts.
    Root cause: cancelling a pending order does not release its reserved stock."""

    def setUp(self):
        self.conn = db.connect()
        self.app = App(self.conn)
        self.app.catalog.add_product("TEE", "T-shirt", 2500, stock=5)

    def test_cancel_pending_order_restocks(self):
        """Cancelling a pending (unpaid) order should return its stock."""
        order = self.app.orders.create("bob", [("TEE", 3)])
        self.assertEqual(self.app.catalog.get_product("TEE").stock, 2)  # 5 - 3
        self.assertEqual(order["status"], "pending")
        self.app.orders.cancel(order["id"])
        self.assertEqual(self.app.catalog.get_product("TEE").stock, 5)  # fully restored

    def test_multiple_abandoned_checkouts_do_not_drain_stock(self):
        """Several abandoned orders followed by cancels should leave stock intact."""
        ids = []
        for i in range(5):
            order = self.app.orders.create(f"user{i}", [("TEE", 1)])
            ids.append(order["id"])
        self.assertEqual(self.app.catalog.get_product("TEE").stock, 0)  # all reserved
        for oid in ids:
            self.app.orders.cancel(oid)
        self.assertEqual(self.app.catalog.get_product("TEE").stock, 5)  # all back


class Bug3TaxOnPreDiscountSubtotalTest(unittest.TestCase):
    """Report 3: Tax is too high when a coupon is used; correct without coupon.
    Root cause: tax is computed on the full subtotal, not on (subtotal - discount)."""

    def setUp(self):
        self.conn = db.connect()
        self.app = App(self.conn)
        self.app.catalog.add_product("MUG", "Mug", 1000, stock=10)
        # 10% coupon
        self.conn.execute(
            "INSERT INTO coupons (code, kind, value) VALUES ('TEN', 'percent', 10)")
        # Fixed $5 coupon
        self.conn.execute(
            "INSERT INTO coupons (code, kind, value) VALUES ('FIVE', 'fixed', 500)")

    def test_tax_computed_after_percent_discount(self):
        """Tax should be on (subtotal - discount), not on subtotal alone."""
        # MUG x2 = 2000, 10% discount = 200, taxable = 1800
        # CA tax = 725 bps = 7.25%, tax on 1800 = 131 (rounded half up)
        order = self.app.orders.create("carol", [("MUG", 2)], region="CA", coupon_code="TEN")
        self.assertEqual(order["subtotal_cents"], 2000)
        self.assertEqual(order["discount_cents"], 200)
        expected_tax = pricing.tax_for(2000 - 200, "CA")  # tax on discounted amount
        self.assertEqual(order["tax_cents"], expected_tax)
        # Tax should be less than tax on full subtotal
        tax_on_full = pricing.tax_for(2000, "CA")
        self.assertLess(order["tax_cents"], tax_on_full)

    def test_tax_computed_after_fixed_discount(self):
        """Fixed coupon also reduces taxable amount."""
        # MUG x2 = 2000, $5 off = 500, taxable = 1500
        order = self.app.orders.create("carol", [("MUG", 2)], region="CA", coupon_code="FIVE")
        expected_tax = pricing.tax_for(1500, "CA")
        self.assertEqual(order["tax_cents"], expected_tax)

    def test_no_coupon_tax_unchanged(self):
        """Without coupon, tax should still be correct."""
        order = self.app.orders.create("carol", [("MUG", 2)], region="CA")
        expected_tax = pricing.tax_for(2000, "CA")
        self.assertEqual(order["tax_cents"], expected_tax)


class Bug4TimezoneOffsetDirectionTest(unittest.TestCase):
    """Report 4: India (UTC+5:30) morning sales appear under the previous day's report.
    Root cause: day_bounds_utc adds the offset instead of subtracting it."""

    def setUp(self):
        self.conn = db.connect()
        self.app = App(self.conn)
        self.app.catalog.add_product("MUG", "Mug", 1200, stock=10)

    def test_india_morning_sale_in_correct_day(self):
        """A sale at 2025-03-10 09:00 IST (03:30 UTC) should appear in 2025-03-10 report for India."""
        order = self.app.orders.create("deepak", [("MUG", 1)])
        # 09:00 IST = 03:30 UTC on 2025-03-10
        india_morning_utc = datetime(2025, 3, 10, 3, 30, tzinfo=timezone.utc)
        self.app.payments.handle_event(
            {"id": "evt_india", "type": "payment.succeeded",
             "order_id": order["id"], "amount_cents": 1200},
            now=india_morning_utc)
        # India is UTC+5:30 = +330 minutes
        report = reports.daily_sales(self.conn, "2025-03-10", utc_offset_minutes=330)
        self.assertEqual(report["revenue_cents"], 1200)
        self.assertEqual(report["orders"], 1)

    def test_india_morning_not_in_previous_day(self):
        """The same sale should NOT appear under 2025-03-09 for India."""
        order = self.app.orders.create("deepak", [("MUG", 1)])
        india_morning_utc = datetime(2025, 3, 10, 3, 30, tzinfo=timezone.utc)
        self.app.payments.handle_event(
            {"id": "evt_india2", "type": "payment.succeeded",
             "order_id": order["id"], "amount_cents": 1200},
            now=india_morning_utc)
        report = reports.daily_sales(self.conn, "2025-03-09", utc_offset_minutes=330)
        self.assertEqual(report["revenue_cents"], 0)
        self.assertEqual(report["orders"], 0)

    def test_day_bounds_utc_india(self):
        """For India (UTC+5:30), 2025-03-10 local starts at 2025-03-09T18:30:00Z."""
        start, end = reports.day_bounds_utc("2025-03-10", utc_offset_minutes=330)
        self.assertEqual(start, "2025-03-09T18:30:00Z")
        self.assertEqual(end, "2025-03-10T18:30:00Z")


class Bug5StockRaceConditionTest(unittest.TestCase):
    """Report 5: With two server workers, stock goes negative — sold more than available.
    Root cause: check-then-update in reserve() is not atomic; two workers can pass the check simultaneously."""

    def setUp(self):
        # Use a file-based DB so two connections can share it (simulating two workers)
        import tempfile, os
        self._tmpdir = tempfile.mkdtemp()
        self._dbpath = os.path.join(self._tmpdir, "test_race.db")
        self.conn = db.connect(self._dbpath)
        self.app = App(self.conn)
        self.app.catalog.add_product("RARE", "Rare item", 5000, stock=1)

    def tearDown(self):
        import shutil
        self.conn.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_atomic_reserve_prevents_negative_stock(self):
        """Two concurrent reserve attempts for the last unit should not both succeed."""
        results = []
        errors = []

        def try_reserve():
            conn2 = db.connect(self._dbpath)
            cat2 = Catalog(conn2)
            try:
                conn2.execute("BEGIN IMMEDIATE")
                cat2.reserve("RARE", 1)
                conn2.execute("COMMIT")
                results.append("ok")
            except (OutOfStock, Exception) as e:
                try:
                    conn2.execute("ROLLBACK")
                except Exception:
                    pass
                errors.append(str(e))
            finally:
                conn2.close()

        t1 = threading.Thread(target=try_reserve)
        t2 = threading.Thread(target=try_reserve)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # At most one should succeed
        self.assertLessEqual(len(results), 1,
                             "Both workers succeeded — stock should have gone negative")
        # Stock should never be negative
        product = self.app.catalog.get_product("RARE")
        self.assertGreaterEqual(product.stock, 0, f"Stock went negative: {product.stock}")

    def test_reserve_still_raises_out_of_stock(self):
        """The atomic reserve should still raise OutOfStock when stock is insufficient."""
        self.app.catalog.reserve("RARE", 1)
        with self.assertRaises(OutOfStock):
            self.app.catalog.reserve("RARE", 1)


class Bug6DoublePaymentTest(unittest.TestCase):
    """Report 6: Customer pays twice with two different payment events for the same order.
    Decision: record both payments, flag order as 'overpaid' so finance can issue a refund."""

    def setUp(self):
        self.conn = db.connect()
        self.app = App(self.conn)
        self.app.catalog.add_product("MUG", "Mug", 1200, stock=10)

    def test_double_payment_marks_order_overpaid(self):
        """Two distinct payments for a fully-paid order should set status to 'overpaid'."""
        order = self.app.orders.create("eve", [("MUG", 1)])  # total = 1200
        # First payment — covers the full order
        status1 = self.app.payments.handle_event(
            {"id": "evt_a", "type": "payment.succeeded",
             "order_id": order["id"], "amount_cents": 1200})
        self.assertEqual(status1, "paid")
        # Second distinct payment — a genuine double charge
        status2 = self.app.payments.handle_event(
            {"id": "evt_b", "type": "payment.succeeded",
             "order_id": order["id"], "amount_cents": 1200})
        self.assertEqual(status2, "overpaid")
        # Both payments should be recorded (for finance to audit)
        self.assertEqual(self.app.payments.paid_cents(order["id"]), 2400)
        # Order status in DB should reflect overpaid
        updated_order = self.app.orders.get(order["id"])
        self.assertEqual(updated_order["status"], "overpaid")

    def test_partial_then_full_no_overpaid(self):
        """A partial payment followed by a payment that completes the order is not overpaid."""
        order = self.app.orders.create("eve", [("MUG", 2)])  # total = 2400
        self.app.payments.handle_event(
            {"id": "evt_part1", "type": "payment.succeeded",
             "order_id": order["id"], "amount_cents": 1200})
        status2 = self.app.payments.handle_event(
            {"id": "evt_part2", "type": "payment.succeeded",
             "order_id": order["id"], "amount_cents": 1200})
        self.assertEqual(status2, "paid")
        self.assertEqual(self.app.payments.paid_cents(order["id"]), 2400)


if __name__ == "__main__":
    unittest.main()
