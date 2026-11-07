# invoicing_io: notes for Claude

Private Django web app for the owner's soap and cleaning-products business in
Kenya: products with units and selling options, stock ledger, invoices (issue,
amend, cancel/replace), payments, dashboard. Posters (Phase 5) are next.

Read `planning/PROGRESS.md` first: it has every decision so far, what each phase
built, and known limitations. `README.md` has setup and the business rules.

## Stack
- Windows PC, Python 3.13 in `.venv`, Django 5.2 LTS, SQLite (PostgreSQL later).
- Bootstrap 5 + TypeScript (strict) built by Vite into `static/dist/` with fixed
  file names. Page scripts are registered in `vite.config.ts`.
- Invoices are PNG images: ReportLab lays out one page and pypdfium2 turns it into a
  picture (`invoices/pdf.py`). Receipts are still PDFs (`payments/pdf.py`). Pillow
  handles uploaded images.
- Settings come from `.env` (never commit it; never print the secret key).

## Commands (PowerShell, venv active)
- Run: `python manage.py runserver`
- Tests: `python manage.py test` (all must pass; 223 now)
- Front end: `npm run typecheck`, `npm run build`
- Migrations: `python manage.py makemigrations --check --dry-run` must be clean.

## Rules the code must keep
- Money is `Decimal`, rounded half-up to cents. Totals are always recalculated on
  the server; the TypeScript preview must match it exactly (BigInt maths).
- Business logic lives in each app's `services.py`; views and templates never
  change status, totals, stock or payments directly.
- Ledgers are append-only: stock movements, payments (reversed, never edited or
  deleted), payment allocations (released, never deleted), invoice revisions.
- Issued invoices change only through `amend_invoice` (keeps a revision) or
  `cancel_invoice`. Invoice lines are snapshots; catalogue edits never alter them.
- Units: ml/L and g/kg convert exactly; count units never convert. Stock is held
  in the product's stock unit (ml, g or a count unit). Packs are never pro-rated.
  All conversions go through `products/units.py` and `products/pricing.py`.
- Every view needs login and a permission (`core.permissions.perm_required`).
- Don't invent business details (names, prices, tax numbers). Tax is off: the
  owner is not VAT-registered and issues general invoices.
- Write tests for every rule you add; keep phone widths (390 px) usable.

## Working with the owner
- Explain changes in plain language; ask before major design decisions or
  anything that rewrites existing data (data migrations).
- Phase 5 (posters) is waiting on the owner's answers: template format
  (Canva/Photoshop export, PNG/JPG/PDF), sizes (square, story, A4) and font
  files (.ttf/.otf).
