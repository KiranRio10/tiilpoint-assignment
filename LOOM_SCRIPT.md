# Loom Video Recording Script & Checklist

Record a 2-3 minute video on Loom (https://www.loom.com). Show your screen and talk through:

---

### Part 1: Introduction (30 seconds)
> *"Hi, my name is M. Kiran Kumar. This is my walkthrough for the Tillpoint engineering assessment. I've resolved all 6 reported issues, written automated unit tests for each root cause, documented all decisions in NOTES.md, and included the full session logs."*

---

### Part 2: Running the Tests (45 seconds)
1. Open terminal in `tillpoint_assignment`:
   ```powershell
   python -m unittest discover -s tests -v
   ```
2. Point out:
   > *"All 21 unit tests are passing — 7 original tests plus 14 new tests in `test_bug_fixes.py` specifically targeting each of the 6 reported problems."*

---

### Part 3: Quick Overview of the 6 Fixes (60 seconds)
1. **Report 1 (Payments)**:
   > *"Added `event_id` check in `payments.py` to make webhook handling idempotent. Retries no longer duplicate payments or inflate daily revenue."*
2. **Report 2 (Inventory Leak)**:
   > *"In `orders.py cancel()`, stock is now released for both `pending` and `paid` orders. Abandoned checkouts no longer lock up inventory."*
3. **Report 3 (Tax calculation)**:
   > *"In `pricing.py`, sales tax is now calculated on `(subtotal - discount)` rather than the full subtotal before discount."*
4. **Report 4 (Timezone offset)**:
   > *"In `reports.py day_bounds_utc()`, changed the offset from `+=` to `-=` to correctly convert local midnight to UTC for positive offsets like India (+5:30)."*
5. **Report 5 (Concurrency race condition)**:
   > *"In `catalog.py reserve()`, replaced check-then-update with an atomic `UPDATE ... WHERE stock >= qty` to eliminate the TOCTOU race with multiple workers."*
6. **Report 6 (Double payment open question)**:
   > *"When two distinct payments exceed the order total, the order status is updated to `overpaid` so finance can identify it and issue a manual refund."*

---

### Part 4: NOTES.md and Git History (30 seconds)
1. Open `NOTES.md` and show all fields filled out.
2. Run in terminal:
   ```powershell
   git log --oneline
   ```
3. Show the commit-per-fix history and conclude:
   > *"Every fix has its own commit and test, and the submission zip is ready in the repository. Thank you!"*
