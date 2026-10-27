# invoicing_io — Phase 0 plan (for approval)

Status: **proposal, nothing built yet.** Prepared 26 Oct 2026 from `PROJECT MASTER PROMPT` (the master prompt replaces the older `planning/tech_stack.png`, which listed JavaScript instead of TypeScript).

Repository state at inspection: `invoicing_io/` contains only `planning/tech_stack.png`. No code, no git repo, no database, so there is nothing existing to preserve or migrate.

---

## 1. Requirements summary

A private Django web app for one soap and cleaning-products business. It has two halves:

| Area | What it must do | Priority |
|---|---|---|
| Invoicing | Pick a customer (saved or one-time) and catalogue products. Prices fill in automatically and can be overridden per line. Totals are computed with Decimal arithmetic on the server. Invoices are numbered safely, issued records can't be edited, and a professional ReportLab PDF can be downloaded. | **Highest** |
| Payments | Record payments manually (cash, M-Pesa, bank and so on), including several partial payments per invoice. Balances and the overdue status are derived from the records. Corrections happen through reversals that leave an audit trail, never deletions. | High |
| Catalogue & customers | CRUD, search, filters, photos and archiving. Archived records stay visible on old invoices. | High |
| Dashboard | Invoiced sales, cash received and outstanding balances, kept clearly separate. Everything is computed from real records. | Medium |
| Posters | Upload approved templates and define editable regions. Rendering is deterministic with Pillow and never redesigns the template. Previews come from the same renderer as exports, and every export is kept with its own version. | Medium |
| AI image tools | Optional and swappable. Nothing is sent externally without your confirmation, and every operation has a non-AI fallback. | Later (Phase 6) |
| Platform | Login required everywhere and private media. Secrets come from env vars. SQLite now, PostgreSQL in production, with backups and a documented restore. | Throughout |

Hard rules carried into every phase: no float money, no client-trusted totals or prices, no deleting financial history, no invented business details, no placeholder buttons, and no paid services without approval.

---

## 2. Decisions I need from you

Each item has a proposed default. If you approve the plan without comment, I'll use the defaults.

| # | Question | Why it matters | Proposed default |
|---|---|---|---|
| D1 | **KRA eTIMS / VAT.** Are you VAT-registered, and do your institutional customers (schools, hospitals) need eTIMS invoices from you? | KRA now cross-checks expense claims against eTIMS records. Buyers can lose the deduction when a supplier's invoice isn't on eTIMS, so institutions often insist on one. This app can't issue a KRA-valid tax invoice unless it's integrated with eTIMS, and that integration is a separate, bigger project. | v1 produces commercial invoices. **Tax is off by default** and the rate and label are configurable. You keep issuing eTIMS invoices through KRA's own tools (e.g. eTIMS Lite) where they're required. An optional "eTIMS invoice no." field on each invoice links the two. Please confirm with your accountant (I'm not a tax adviser). |
| D2 | **Stock tracking in v1?** | Adds a stock ledger and rules for deducting and restoring stock. | **Off in v1.** The model is ready (`track_stock` flag) and the ledger gets switched on in Phase 4 if you want it. |
| D3 | **Product sizes.** Is each size its own product (e.g. "Liquid soap 5 L" and "Liquid soap 20 L"), or is it one product with variants? | Affects the catalogue and the poster product picker. | **Each sellable size is its own product row** with a `package_size` field. That's simpler and matches how invoice lines work. |
| D4 | **Fixing an issued invoice.** | Issued invoices must not be silently rewritten. | **Cancel and reissue.** You cancel the original (a reason is required and it's logged) and a new invoice is created that links back to it. Credit notes can come later if your accountant prefers them. |
| D5 | **Can one payment cover several invoices?** (e.g. a school pays three invoices in one M-Pesa transfer) | Decides whether the payment allocation table is needed now. | **Yes, build allocations from the start.** It's cheap now and painful to retrofit later. |
| D6 | **Invoice number format.** | Hard to change once invoices are issued. | `INV-2026-0001`. The prefix is configurable, the sequence resets each year, and **a number is assigned only when the invoice is issued** (drafts show "DRAFT"), so abandoned drafts never leave gaps. |
| D7 | **Your poster templates.** What are they (Canva or Photoshop exports? PNG/JPG/PDF?), what sizes do you need (square, story, A4), and do you have the font files (.ttf/.otf)? | Pillow can only draw text in fonts it has as files. Different aspect ratios need different templates, because cropping one template into another shape would change the design. | One template version = one fixed canvas size. You upload a flat background image, plus an optional transparent overlay for elements that sit above the product photo. You upload the fonts once. |
| D8 | **Python, Node and Git on your PC.** | I can't run commands on your computer from this session (details below). | You run `py --version`, `node --version` and `git --version` and tell me the output. |

---

## 3. Proposed data model

Conventions: money is `DecimalField(max_digits=12, decimal_places=2)` and quantities are `DecimalField(max_digits=10, decimal_places=2)` so fractional litres work. Rounding is `ROUND_HALF_UP` to cents, per line. Every table gets `created_at` and `updated_at`. Financial records use `on_delete=PROTECT` and are never hard-deleted. `TIME_ZONE = "Africa/Nairobi"` and `USE_TZ = True`.

```
accounts.User (AbstractUser)            custom user from day 1 (can't be added later without pain)

core.BusinessSettings (singleton pk=1)
  name, logo, phone, email, address, kra_pin?, registration_no?
  currency="KES", invoice_prefix="INV", number_padding=4, reset_yearly=True
  default_payment_terms_days, default_terms_text, default_invoice_notes, payment_instructions
  tax_enabled=False, tax_label, tax_rate, prices_include_tax
  poster_default_format, poster_default_jpeg_quality
core.AuditLog
  actor→User, action, content_type+object_id, changes(JSON), reason, created_at

products.Category            name (unique), is_active
products.Product
  name, sku (unique, nullable), category→Category PROTECT, description
  unit (bottle/litre/container/piece/…), package_size (e.g. "500 ml")
  default_price ≥0, cost_price? ≥0, photo
  track_stock=False, stock_qty, low_stock_threshold      (ledger only if D2 = yes)
  is_active
  index(name), index(category, is_active)

customers.Customer
  kind (individual/organization), name, contact_person, phone, email
  billing_address, tax_pin?, notes, is_active
  index(name), index(phone)

invoices.InvoiceSequence     prefix, year, last_number          unique(prefix, year)
invoices.Invoice
  number (unique, NULL while draft), status: DRAFT | ISSUED | CANCELLED
  customer→Customer NULL PROTECT          (NULL = one-time customer)
  customer_name/phone/email/address/tax_pin   ← snapshot, always filled
  business_snapshot (JSON)                    ← seller details frozen at issue
  issue_date, due_date?, currency, payment_terms, notes
  invoice_discount ≥0, tax_label, tax_rate, prices_include_tax   ← frozen at issue
  subtotal, discount_total, tax_total, total  ← computed by service, stored at issue
  etims_reference? (see D1)
  replaces→Invoice NULL  (cancel & reissue chain)
  issued_at/by, cancelled_at/by, cancel_reason, created_by
  CHECK: status≠ISSUED or number IS NOT NULL;  index(status, issue_date), index(customer)
invoices.InvoiceLine
  invoice→Invoice CASCADE, position, product→Product NULL PROTECT
  description, unit, package_size        ← snapshots
  catalogue_price (snapshot, shows that a price was overridden)
  quantity >0, unit_price ≥0, line_discount ≥0, line_total
  CHECK: quantity>0, unit_price≥0, line_discount ≤ quantity×unit_price

payments.PaymentMethod       name, requires_reference, is_active
                             (seeded: Cash, M-Pesa, Bank transfer, Cheque; editable)
payments.Payment
  customer→Customer NULL, payer_name, amount >0, payment_date, method→PaymentMethod
  reference?, notes, submission_token (UUID, unique)  ← blocks double-submit
  is_reversed, reversed_at/by, reversal_reason
  UNIQUE(method, reference) where reference ≠ ""      ← same M-Pesa code can't be entered twice
payments.PaymentAllocation
  payment→Payment PROTECT, invoice→Invoice PROTECT, amount >0
  rule: sum(allocations) ≤ payment.amount; per-invoice allocations ≤ invoice balance

posters.FontAsset            name, file (.ttf/.otf)
posters.PosterTemplate       name, category, is_active
posters.TemplateVersion
  template→PosterTemplate, version_no, background_image, overlay_image?
  width, height (read from the image, never typed), is_locked (locks on first use)
  unique(template, version_no)
posters.TemplateField
  version→TemplateVersion, key (product_name, price, promo_price, product_image, logo, phone, …)
  label, kind (text | price | image), x, y, w, h (px on the original canvas), z_index
  text: font→FontAsset, size, min_size, color, align, max_lines, overflow (shrink | wrap | reject)
  image: fit (contain | cover), required
  default_source (e.g. "product.name", "settings.phone")
posters.PosterProject
  template_version→TemplateVersion PROTECT, product→Product NULL, name, field_values (JSON)
posters.PosterExport
  project→PosterProject, export_no, file, format (PNG/JPEG), scale, width, height,
  jpeg_quality, field_values snapshot, created_at
  unique(project, export_no) → new file every time, never overwritten
```

### Derived values (computed from records, not stored)
- **Amount paid** = sum of allocations from non-reversed payments.
- **Balance** = total − amount paid.
- **Payment state**: Unpaid, Partially paid, Paid, or **Overdue** (issued, balance > 0, and today is after `due_date`). This is computed with database annotations, so lists can filter on it without a stored field that could fall out of sync. Cancelled invoices are excluded from receivables.

### Key rules enforced in `services.py` (not in views or templates)
- `calculate_invoice(lines, discount, tax)` is a pure Decimal function, unit-tested in isolation, and used both for draft saves and at issue time.
- `issue_invoice()` runs inside `transaction.atomic()`. It re-validates, recalculates, freezes the snapshots, and takes the next number from `InvoiceSequence` using `select_for_update()`. The `unique(number)` constraint is the final guard, with a retry if it's hit.
- `record_payment()` and `reverse_payment()` run atomically and write `AuditLog` rows.
- Issued invoices reject edits at the model/service level, not just in the UI.

---

## 4. Directory structure

`invoicing_io/` becomes the repository root (same layout as the master prompt, minus the extra `business_app/` level):

```
invoicing_io/
  config/              settings.py (env-driven), urls.py, wsgi.py, asgi.py
  accounts/            custom User, login/logout templates
  core/                BusinessSettings, AuditLog, dashboard, money utils, protected-media view
  products/  customers/  invoices/  payments/  posters/
      each: models.py, forms.py, views.py, urls.py, services.py, admin.py,
            templates/<app>/, tests/
  invoices/pdf.py      ReportLab renderer
  posters/rendering.py Pillow compositor (single source for preview + export)
  templates/           base.html, partials (sidebar, messages, pagination, empty states)
  static/
    ts/                main.ts, invoice-form.ts, poster-editor.ts, lib/money.ts
    css/app.css        custom theme on top of Bootstrap
    images/
    dist/              ← Vite build output (git-ignored)
  media/               ← uploads (git-ignored, served only via login-protected view)
  planning/            this plan, tech_stack.png
  manage.py  requirements.txt  package.json  tsconfig.json  vite.config.ts
  .env.example  .gitignore  README.md
```

Tests live inside each app (`products/tests/…`) so Django finds them automatically. A top-level `tests/` folder is only used for cross-app end-to-end tests.

---

## 5. How the pieces fit together

**Django templates + Bootstrap.** Every page is server-rendered. `base.html` loads one compiled CSS bundle and one JS bundle, plus a page-specific script where needed (`invoice-form.js`). Bootstrap is installed through npm and bundled by Vite, so there's no CDN and pages work offline in development.

**TypeScript + Vite.** Vite builds `static/ts/*.ts` into `static/dist/*.js` (and `main.css`) with **fixed filenames**. Django includes them with the normal `{% static 'dist/main.js' %}`. In production, WhiteNoise's manifest storage adds cache-busting hashes, so the `django-vite` package isn't needed.
- Development: run two terminals, `npm run dev` (= `vite build --watch`) and `python manage.py runserver`.
- Type checking: `npm run typecheck` (`tsc --noEmit`, strict mode).
- Data passes from Django to TS through `{{ data|json_script:"id" }}`, e.g. the product list with default prices for the invoice form. No inline JS in templates.

**Invoice form.** Django formsets hold the line items. TypeScript adds and removes rows, auto-fills the description and price when a product is picked, and shows live totals. The client calculates in **integer cents** for the preview only. The server ignores every client-sent total and recalculates. Keyboard flow: Enter or Tab moves through product → qty → price, with a shortcut to add a row.

**PDF.** `invoices/pdf.py` uses ReportLab Platypus. A `Table` with `repeatRows=1` repeats the header row on every page, "Page x of y" is drawn in the footer, and the logo and colours come from BusinessSettings. The PDF is generated on request from the invoice's frozen snapshot, so it's identical every time, never stored in a public folder, and only served to logged-in users. Filename: `INV-2026-0001_<customer-slug>.pdf`.

**Posters.** `posters/rendering.py` takes a TemplateVersion and field values. It opens the untouched background, pastes each image region (fit or cover inside its box), lays the overlay on top, then draws text with the template's fonts and overflow rules, and returns a Pillow image. The **preview endpoint and the export use the same function**. TypeScript sends field values to the preview endpoint (debounced) and shows the returned image, so what you see is exactly what you export. Exports are made at the native size or a scale factor; the aspect ratio never changes. The region editor (Phase 5) is a TypeScript canvas where you drag rectangles over the template image.

**Private files.** All of `/media/` is served through a `@login_required` Django view that returns a `FileResponse`, never as a public folder. That's fine for a single business's traffic, and it can be swapped for signed cloud-storage URLs later.

---

## 6. Dependencies and setup (Windows)

Current stable versions, checked 26 Oct 2026. Exact pins go into `requirements.txt` and `package-lock.json` when Phase 1 runs.

| Package | Version | Why this one |
|---|---|---|
| Python | 3.13 | Supported by everything below (Pillow 13 needs ≥ 3.11) |
| Django | **5.2 LTS** (5.2.18) | Long-term support with security fixes until April 2028. Django 6.1 is newer but not LTS; we can move to 6.2 LTS when it ships (April 2027). |
| Pillow | 13.0 | Image processing and posters |
| ReportLab | 5.1 | PDFs |
| django-environ | 0.14 | `.env` and `DATABASE_URL` parsing (one library covers both) |
| WhiteNoise | 6.12 | Serves static files in production |
| psycopg, gunicorn | — | Added in Phase 7 only (production on Linux) |
| Node.js | 24 LTS (≥ 22.12 also OK) | Vite 8 requires Node ^20.19 or ≥ 22.12 |
| Vite | 8.3 | TypeScript and CSS bundling |
| TypeScript | 6.0 | Type checking. I'm choosing 6.0 over 7.0 (the new Go-based compiler) as the safer option for tooling compatibility. |
| Bootstrap | 5.3.8 (+ @popperjs/core) | UI |

No other Python dependencies in v1. Tests use Django's built-in runner, and test data comes from small helper functions rather than a factory library.

Setup, once Phase 1 has created the files (PowerShell, inside `invoicing_io`):

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
npm install
copy .env.example .env                # then set SECRET_KEY
npm run build
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver            # http://127.0.0.1:8000
# second terminal while developing:  npm run dev
python manage.py test                 # full test suite
```

**How I'll work (a limitation you should know about):** this session can read your project folder and copy files into it, but it **can't run commands on your PC**. So I'll write and test each phase in my own Linux workspace, using the same Python, Node and package versions, and then copy the files into `invoicing_io`. After each phase you run the commands above and the test suite on Windows to confirm it works there too. I'll flag anything Windows-specific.

---

## 7. Decisions that affect production

1. **Hosting needs persistent storage.** Several platforms (e.g. Render web services, Heroku) wipe local disk on every deploy, which would silently delete uploads. That leaves two choices: (a) a VPS or a platform with a persistent volume, or (b) object storage such as S3 or R2 via `django-storages`. The media layer is kept behind Django's storage API so either works. The host gets chosen in Phase 7, with costs listed for your approval.
2. **PostgreSQL in production.** SQLite allows only one writer at a time, and `select_for_update()` does nothing on it. The invoice-numbering code is written for PostgreSQL's row locking and is still correct on SQLite because SQLite serializes writes. Before deploying I'll run the full test suite against PostgreSQL and rehearse a `dumpdata`/`loaddata` move of your real data.
3. **Config through env vars only**: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, and the storage credentials. `python manage.py check --deploy` must come back clean.
4. **HTTPS, secure cookies, HSTS** are switched on when `DEBUG=False`.
5. **Login brute-force protection.** Since the app will be public on the internet, I'll add rate limiting (`django-axes` or a small custom throttle) in Phase 7.
6. **Backups.** A nightly `pg_dump` plus a media sync to separate storage, with a restore drill that's documented and actually tested.
7. **Email.** Password reset by email needs an email provider. v1 skips it; as admin you reset passwords with `manage.py changepassword`.
8. **Custom User model and the money/decimal schema** are set in Phase 1 and are expensive to change later, which is why they're being fixed now.

---

## 8. Phase 1 — executable steps

| # | Step | Verification |
|---|---|---|
| 1.1 | Confirm Python 3.13, Node ≥ 22.12, and Git on your PC (D8). | The version commands print the expected versions. |
| 1.2 | `git init`. Add `.gitignore` (`.venv/`, `node_modules/`, `.env`, `db.sqlite3`, `media/`, `static/dist/`, `staticfiles/`). | `git status` shows no junk files. |
| 1.3 | `requirements.txt` (pinned), venv, `django-admin startproject config .` | `python -m django --version` prints 5.2.x. |
| 1.4 | Env-driven `settings.py` with django-environ, plus `.env.example`. Nairobi timezone, `en-ke` locale, upload limit (default 5 MB). | Server starts with `.env`. With `DEBUG=False` and no `SECRET_KEY`, it refuses to start. |
| 1.5 | `accounts` app with a custom `User`, registered **before the first migration**. | `migrate` succeeds and `AUTH_USER_MODEL` is set. |
| 1.6 | Create the `core`, `products`, `customers`, `invoices`, `payments` and `posters` apps. In this phase only `BusinessSettings` and `AuditLog` get models. | `makemigrations` and `migrate` are clean, and `check` reports 0 issues. |
| 1.7 | `package.json`, `tsconfig.json` (strict), `vite.config.ts` with multiple entries and fixed output names. Bootstrap imported in `main.ts`, plus `app.css`. | `npm run build` produces `static/dist/main.js` and `main.css`, and `npm run typecheck` passes. |
| 1.8 | `base.html`: responsive sidebar (offcanvas on mobile), top bar with business name and logout, flash messages, and the theme. Sections not built yet are shown **disabled with a "Coming in Phase N" label**, so there are no dead links. | Manual check at desktop and phone widths. The sidebar toggle (TypeScript) works. |
| 1.9 | Authentication: Django `LoginView`, POST-only logout, `LoginRequiredMiddleware` (Django 5.1+), so every page needs a login unless explicitly exempt. Session cookie: HttpOnly, SameSite=Lax, 12-hour age. | Tests: an anonymous user → redirected to login for every URL; a logged-in user → 200; GET to logout does nothing. |
| 1.10 | Protected media view and upload validation (images: real image type checked with Pillow, size limit, safe random filenames). | Tests: anonymous media request → redirect; a fake `.png` that isn't an image → rejected. |
| 1.11 | Business Settings page (singleton form, logo upload) and the dashboard shell with empty states. | Edit, save and reload keep the values. The dashboard shows "No invoices yet". |
| 1.12 | README (setup, dev workflow, running tests) and `planning/PROGRESS.md` (done / next / known issues). | You can follow the README on a fresh checkout. |
| 1.13 | Full check: `python manage.py test`, `npm run typecheck`, `npm run build`, `manage.py check`. | Everything passes in my workspace **and** when you run it on your PC. |

**Phase 1 is done when** you can install it from the README, log in, see the styled responsive layout, edit and save your business settings, log out, and confirm that nothing is reachable without logging in.

---

### Sources
- KRA eTIMS rules (threshold revoked, expense validation from 2026, eTIMS Lite): https://beancount.io/blog/2026/07/22/kenya-kra-etims-income-expense-validation-guide · https://assets.kpmg.com/content/dam/kpmgsites/ke/pdf/thought_leaderships/tax/2026/eTIMS.pdf.coredownload.inline.pdf · https://thekenyatimes.com/business/kra-launches-simplified-etims-solutions-for-businesses/
- Package versions checked against PyPI and the npm registry on 26 Oct 2026.
