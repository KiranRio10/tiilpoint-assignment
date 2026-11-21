"""Payment provider webhooks. The provider retries a delivery until it gets a 2xx, so events can repeat."""
from . import db


class PaymentError(Exception):
    pass


class Payments:
    def __init__(self, conn):
        self.conn = conn

    def handle_event(self, event, now=None):
        """event: dict(id, type, order_id, amount_cents). Returns the order's status after the event."""
        if event.get("type") != "payment.succeeded":
            return None
        order = self.conn.execute("SELECT * FROM orders WHERE id = ?", (event["order_id"],)).fetchone()
        if order is None:
            raise PaymentError(f"payment for unknown order {event['order_id']}")
        if order["status"] == "cancelled":
            raise PaymentError(f"order {order['id']} is cancelled")
        # Idempotency: ignore retries of the same event from the payment provider.
        existing = self.conn.execute(
            "SELECT 1 FROM payments WHERE event_id = ?", (event["id"],)
        ).fetchone()
        if existing is not None:
            return order["status"]
        self.conn.execute(
            "INSERT INTO payments (order_id, event_id, amount_cents, received_at) VALUES (?, ?, ?, ?)",
            (order["id"], event["id"], event["amount_cents"], db.to_iso(now or db.utcnow())),
        )
        paid = self.paid_cents(order["id"])
        if order["status"] == "pending" and paid >= order["total_cents"]:
            self.conn.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order["id"],))
            return "paid"
        # Overpayment: a genuinely distinct second payment pushed the total above what the order costs.
        if paid > order["total_cents"] and order["status"] in ("paid", "shipped"):
            self.conn.execute("UPDATE orders SET status = 'overpaid' WHERE id = ?", (order["id"],))
            return "overpaid"
        return order["status"]

    def paid_cents(self, order_id):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS paid FROM payments WHERE order_id = ?", (order_id,)
        ).fetchone()
        return row["paid"]
