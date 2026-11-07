# Progress log

## Decisions

| #  | Decision | Status |
|----|----------|--------|
| D1 | eTIMS / VAT | **Decided 27 Oct 2026:** general (commercial) invoices titled "Invoice". The owner is not VAT-registered and trades as an individual, no company yet; no customer has asked for eTIMS. Tax stays off. The optional eTIMS reference field stays available for later. Revisit when a company is registered or a customer asks for eTIMS. |
| D2 | Stock tracking in v1 | **Yes** (changed from the plan's default, 26 Oct 2026). Built in Phase 1. |
| D3 | One product row per size | Default accepted |
| D4 | Corrections = cancel and reissue | Default accepted |
| D5 | One payment can cover several invoices (allocations) | Default accepted; tables built |
| D6 | `INV-YYYY-NNNN`, number assigned on issue, yearly reset | Default accepted |
| D7 | Poster template format, sizes, fonts | **Open.** Needed before Phase 5. The `posters` app will be created then. |
| D8 | Python/Node/Git versions on the PC | Python 3.13.1, Node 24.19.0, Git 2.47.1 — all compatible |

Changes to the original plan:
- A separate `inventory` app holds the stock ledger, keeping stock rules out of `products`.
- Fonts are self-hosted (IBM Plex Sans via npm), so the app needs no external CDN.

## Phase 1 — foundation ✅ (26 Oct 2026)

Done:
- Django 5.2 LTS project. Settings come from the environment (`.env`), with a Nairobi time zone and production security switches.
- A custom `User` model, created before the first migration.
- **All data models and initial migrations** (posters excluded until D7):
  - core: BusinessSettings (single row), AuditLog
  - products: Category, Product
  - customers: Customer
  - inventory: StockMovement (append-only ledger)
  - invoices: InvoiceSequence, Invoice, InvoiceLine
  - payments: PaymentMethod (seeded with Cash, M-Pesa, Bank transfer, Cheque), Payment, PaymentAllocation
- Database constraints:
  - non-negative money
  - quantity > 0
  - unique invoice numbers
  - one SALE and one SALE_REVERSAL per invoice line
  - no duplicate M-Pesa or bank reference
  - each stock movement's direction must match its kind
  - only one BusinessSettings row
- Services:
  - Decimal invoice calculations (line discount, invoice discount, tax inclusive or exclusive)
  - concurrency-safe numbering
  - draft → issue → cancel → replacement
  - stock in, stock out (with a reason), count adjustment
  - stock deducted on issue and restored on cancel
  - `check_stock` ledger verification
- Integrity guards:
  - issued and cancelled invoices and their lines can't be edited
  - payments can't be edited or deleted, only reversed
  - stock movements and audit entries are append-only
  - a product's stock quantity can't be overwritten by saving an out-of-date copy of the product
- Sign-in and sign-out:
  - every page requires a login (`LoginRequiredMiddleware`)
  - logout only works through POST
  - sessions last 12 hours
- Responsive layout: a green sidebar on desktop and an off-canvas menu on phones. Sections not built yet are shown but disabled, so there are no dead links.
- Settings page: sections, validation, logo upload with preview, and an audit entry for every change.
- Dashboard: setup checklist and low-stock alerts.
- Uploads are served only to signed-in users, with path-traversal protection, real-image checks and size limits.
- TypeScript (strict) + Vite + Bootstrap pipeline with fixed output names. Shared modules: a confirmation dialog and a double-submit guard.
- Read-only admin for ledgers and financial records. Products, customers and payment methods can be edited there.

Verified (in the Linux workspace, using the same Python, Django, Node and package versions):
- `python manage.py test` → **96 tests pass**
- `npm run typecheck` → clean
- `npm run build` → clean
- `makemigrations --check` → no changes
- `check --deploy` with `DEBUG=False`: only the two warnings that were deliberately relaxed for the local test (no SSL redirect, no HSTS preload)
- `collectstatic` with WhiteNoise's hashed files works, and the font links are rewritten correctly
- Browser checks at 1366 px and 390 px wide:
  - sign in, dashboard, and settings save, error and upload paths all behave correctly
  - the phone menu works and there's no horizontal scrolling
  - the confirmation dialog's Go back and Confirm both behave correctly
  - no console errors

**Verified on Windows (27 Oct 2026):** 96 tests pass on the owner's PC (Python 3.13.1, Node 24.19).

## Phase 2 — products, customers and stock screens ✅ (27 Oct 2026)

Done:
- **Products:** list with search (name, SKU, size), filters (category, stock level, active/archived), pagination and thumbnails. Add (with optional opening stock), edit, detail page, archive and restore. Every change is written to the audit log; price changes record the old and new value.
- **Product photos:**
  - turned the right way up
  - all metadata removed, including GPS location from phones
  - resized to at most 1600 px
  - transparent PNGs kept as PNG (for posters later)
  - a 320 px WebP thumbnail is made for lists
  - replaced or removed photos are deleted from disk once the change is saved
- **Categories:** add, rename, archive and restore, with a case-insensitive duplicate check.
- **Customers:** list with search (name, contact, email or phone; phone matching ignores spaces and the `+254`/`0` prefix), filters (type, status), add, edit, archive and restore. A non-blocking warning appears when a customer has the same name or phone as an existing one. The detail page shows contact details, what they owe, how much of it is overdue, total invoiced, and invoice history (it fills up from Phase 3).
- **Stock:**
  - overview with low and out-of-stock counts and filters, with quick stock in, stock out and count buttons on each row
  - stock in: quantity, optional cost per unit (pre-filled from the product), reference, notes
  - stock out: a reason is required (damaged, expired, own use, sample, lost, other)
  - single-product count
  - **stock take:** count many products on one page; only differences are recorded, and nothing is saved if any entry is invalid
  - full history with filters (product, type, date range)
  - live hints show current stock and what it will become, and warn when a stock out exceeds what's recorded
- **Double-submit protection:** each stock form carries a one-time token stored on the movement (with a unique constraint), so a double tap or a resubmitted form can't record stock twice. This covers the stock take too.
- **Permissions on every view**, ready for staff accounts: the owner (superuser) can do everything; a staff account only gets what its permissions allow, otherwise it sees a 403 page and the links are hidden.
- The sidebar shows Customers, Products and Stock, with a badge counting products that are low or out of stock. The dashboard checklist now links to the new screens.
- Migrations: `products/0002` (thumbnail field, friendlier duplicate messages) and `inventory/0002` (submission token and an index). Both only add things; no existing data changes.

Verified (in the Linux workspace):
- **137 tests pass**: 41 new ones covering products, categories, photos, customers, stock forms, stock take, double submits and permissions.
- `npm run typecheck`, `npm run build` and `makemigrations --check` are clean, and production `collectstatic` builds the new scripts.
- Browser checks at 1366 px and 390 px wide on every new page:
  - filters apply as soon as they change
  - the stock fields hide when "Track stock" is off
  - the stock hints and stock take differences update as you type
  - no console errors and no sideways scrolling (the stock take shows differences under each box on phones)

## Phase 3: invoices ✅ (27 Oct 2026)

Done:
- **Invoice editor** (new or draft):
  - pick a saved customer, whose contact details are shown, or type a one-time customer
  - product lines fill in name, unit and price, and the price can be overridden for this invoice only (the review page shows the usual price)
  - free-text lines, line discounts and a whole-invoice discount
  - add and remove lines; on a phone the lines stack as cards
  - **Enter** moves to the next box, and at the end of the last line adds a new line
  - live totals computed exactly in whole cents; the server recalculates everything on save
  - stock hints per line ("57 in stock", "not enough to issue")
  - a warning before leaving the page with unsaved changes
  - "Save and review" and "Save and keep editing"
- Drafts always follow the current Settings for currency and tax. Lines are saved in the order shown. Archived products can't be added to new invoices but stay on drafts that already have them.
- **Review page:**
  - shows the invoice as the customer will see it
  - warns before issuing if stock is short, and blocks issuing if business details are missing
  - Issue asks for confirmation, then assigns the number, freezes the details and deducts stock
- **After issue:** view, print or download the PDF; cancel with a reason (stock returns), alone or together with a replacement draft; **duplicate** any invoice as a new draft at today's prices (for repeat orders); delete drafts. History shows who created, issued and cancelled each invoice.
- **Invoice list:**
  - search by number or customer
  - filters: status (draft, unpaid, partially paid, overdue, paid, cancelled, not fully paid), customer, date range
  - shows total, paid and balance per invoice, plus totals for the issued invoices matching the filters
- **PDF (ReportLab):**
  - logo and seller details frozen at issue time
  - bill-to block with an "amount due" box
  - item table whose header repeats on every page, totals, payment details, terms and notes
  - "Page x of y" footer
  - DRAFT and CANCELLED watermarks
  - all user text escaped so symbols print as typed
  - predictable filename (`INV-2026-0001_customer-name.pdf`); opens in the browser to print, or downloads
- **New permissions** "Can issue invoices" and "Can cancel issued invoices" (`invoices/0002`), so a future staff account could prepare drafts without issuing them.
- Links to invoices from customer pages (plus a "New invoice" button there) and from stock history. Invoices appears in the sidebar.
- `manage.py test` output is now quiet: tests that deliberately trigger 403/404s no longer print tracebacks.

Verified (Linux workspace):
- **169 tests pass** (32 new for the editor, lifecycle, list filters, permissions and PDFs).
- The PDF tests check the content, multi-page numbering with a repeated table header, watermarks, frozen seller details, escaping, tax and discount rows, the embedded logo and the download filename.
- typecheck, build and `makemigrations --check` are clean.
- Browser run-through at 1366 px and 390 px wide: create → auto-fill → Enter adds a line → remove and renumber → live totals → save → stock warning → issue → PDF → cancel and replace. No console errors and no sideways scrolling.

## Units and invoice amendments (between Phases 3 and 4) — done

Requested: unit-aware quantities, pricing and stock (ml/L, g/kg, count
units), and editing issued invoices "in case of omissions". Decisions:
corrections are **amendments that keep history**; stock for liquids/powders
is kept in ml/g; packs are never pro-rated. Existing products were converted
by a data migration (approved plan in `UNITS_AND_AMENDMENTS_PLAN.md`).

Built:
- `products/units.py` (exact conversions, friendly display), `products/pricing.py`
  (one place that turns "quantity + unit + selling option" into price quantity
  and stock quantity). Products have a stock unit and SellingOptions (pack or
  measure); Unit table seeded with ml, L, g, kg and common count units (new
  count units can be added in Data admin).
- Invoice lines snapshot the option name, quantity and unit entered, price
  unit, conversion factor, price quantity and stock quantity. Editor has a
  "Sold as" list and a unit list per line; the live preview uses exact BigInt
  fractions and matches the server (checked on the acceptance cases).
- Amend: `invoices.amend_invoice` archives the current version
  (InvoiceRevision, append-only), returns its stock, saves the new lines and
  takes their stock, all-or-nothing; superseded lines are kept (stock history
  points at them). Revision page and SUPERSEDED PDF. New permission
  "Can amend issued invoices".
- Stock forms take L/ml/packs; stock take in friendly units; customer return
  page; history shows friendly amounts plus what was typed.
- README section "Setting up your products".

Verified:
- **201 tests pass**, including the 12 acceptance cases
  (`invoices/tests/test_units_acceptance.py`) and 13 amendment tests
  (`invoices/tests/test_amend.py`).
- Migration re-run on a fresh copy of the live database: Soap 50 L with
  "5 L container" at 500; Jik 23 L with "1L container" at 1,300; INV-2026-0001
  unchanged at 750; no ledger discrepancies.
- typecheck, build, `makemigrations --check` clean. Browser run at 1366 px and
  390 px: mixed-unit invoice → issue → amend → revision page and PDF → stock in
  by container → stock take in L. No console errors, no sideways scrolling.

## Phase 4 — payments and dashboard — done

Built:
- **Record payment** from an invoice, a customer or the Payments page: customer
  (or a one-time payer), amount, date, method, reference, notes, and one box per
  unpaid invoice. Typing the amount fills the invoice you came from first, then
  oldest first; what's left is shown as credit. M-Pesa/bank references are
  required for those methods and can't repeat; a per-form token blocks double saves.
- **Payment page**: where the money went, credit left, "Apply the credit" to the
  customer's unpaid invoices, **Reverse** with a reason (new permission "Can
  reverse payments"), and a **receipt PDF** (A5, receipt no. PAY-000123,
  REVERSED watermark when reversed).
- **Invoice page**: payments on the invoice, Record payment, and one-click
  "Use KES … credit" buttons for the customer's unused credit.
- **Cancelling** an invoice with payments is now allowed: allocations are
  *released* (kept on record with a reason) and the money becomes credit;
  issuing the replacement applies it automatically (`payments/0003`).
- **Customer page**: paid on invoices, unused credit, recent payments.
- **Payments list** with search (payer, reference, invoice no.), method, customer,
  dates, "with unused credit" and "reversed" filters, and the total received.
- **Dashboard**: invoiced vs received today / this week / this month, owed and
  overdue (linked to the filtered invoice list), drafts waiting, recent invoices
  and payments, best sellers this month, low stock. Staff without invoice and
  payment permissions see only the stock part.
- Fixed: the double-submit guard broke on forms with a field called "method"
  (payment form, selling-option form).

Verified:
- **219 tests pass** (18 new: recording, splitting, credit, rules, duplicate
  references and submissions, immutability, reversal, cancel → replacement,
  amend-below-paid, views, permissions, receipt, dashboard figures).
- Migrations applied to a fresh copy of the live database; browser run at 1366 px
  and 390 px: pay the existing one-time invoice partly from its page, record an
  M-Pesa payment that fills an invoice and leaves credit, receipt PDF, list,
  customer page, dashboard. No console errors, no sideways scrolling.

## Next: Phase 5 — posters
- Needs your answers first (D7): what your poster templates are (Canva/Photoshop
  exports? PNG/JPG/PDF?), which sizes (square, story, A4) and whether you have the
  font files (.ttf/.otf).

## Known issues and limitations
- SQLite ignores `select_for_update()`. Invoice numbering and stock stay safe because they rely on single-statement `F()` updates plus unique constraints. Real concurrency tests against PostgreSQL are planned for Phase 7.
- Bulk `QuerySet.update()` and `.delete()` bypass the model-level edit guards. Project code doesn't use them on protected models; the database constraints still apply.
- There's no rate limiting on login attempts yet (Phase 7) and no password reset by email (no email provider). Reset passwords with `python manage.py changepassword <username>`.
- Page scripts share small chunks (`dist/chunks/decimal.js`, `units.js`) that aren't cache-busted in production; they're tiny and rarely change. To revisit in Phase 7 if needed.
- Jik was converted with the generic rule ("1L container" at KES 1,300, stock 23 L); the owner should confirm that's how it's sold.
- Invoice quantities allow 2 decimal places (type 125 g rather than 0.125 kg).
