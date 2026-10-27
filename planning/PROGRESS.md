# Progress log

## Decisions

| #  | Decision | Status |
|----|----------|--------|
| D1 | eTIMS / VAT | **Open.** Tax is off by default and an optional eTIMS reference field is already on invoices, so nothing built so far depends on the answer. Needed before Phase 3 (invoice PDF wording and layout). |
| D2 | Stock tracking in v1 | **Yes** (changed from the plan's default, 26 Oct 2026). Built in Phase 1. |
| D3 | One product row per size | Default accepted |
| D4 | Corrections = cancel and reissue | Default accepted |
| D5 | One payment can cover several invoices (allocations) | Default accepted; tables built |
| D6 | `INV-YYYY-NNNN`, number assigned on issue, yearly reset | Default accepted |
| D7 | Poster template format, sizes, fonts | **Open.** Needed before Phase 5. The `posters` app will be created then. |
| D8 | Python/Node/Git versions on the PC | **Waiting** for the user's output |

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

**Not yet verified on Windows.** Run the README setup and `python manage.py test` on the PC to confirm.

## Next: Phase 2 — products, customers and stock screens
- Product list (search, filters by category and status, low-stock badge), create, edit, archive, photo upload.
- Category management.
- Stock screens: stock in, stock out with a reason, stock count, and per-product movement history.
- Customer list (search by name or phone), create, edit, archive, detail page.
- Tests for each form and view.

## Known issues and limitations
- SQLite ignores `select_for_update()`. Invoice numbering and stock stay safe because they rely on single-statement `F()` updates plus unique constraints. Real concurrency tests against PostgreSQL are planned for Phase 7.
- Bulk `QuerySet.update()` and `.delete()` bypass the model-level edit guards. Project code doesn't use them on protected models; the database constraints still apply.
- Cancelling an invoice that already has payments is blocked for now. Moving payments to the replacement invoice comes in Phase 4.
- There's no rate limiting on login attempts yet (Phase 7) and no password reset by email (no email provider). Reset passwords with `python manage.py changepassword <username>`.
- Opening stock can't be entered through the UI until the Phase 2 stock screens exist. The admin is deliberately read-only for stock.
