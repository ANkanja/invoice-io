# invoicing_io

A private web app for running a soap and cleaning-products business: products,
stock, customers, invoices (as images), payments, and template-based promotional
posters.

Built with Django 5.2 LTS, server-rendered templates, Bootstrap 5.3 and
TypeScript (compiled by Vite). SQLite is used for local development and
PostgreSQL in production.

> **Status:** Phases 1–4 (foundation; products, customers and stock; invoices; payments and dashboard) are complete. See `planning/PROGRESS.md` for
> what's done, what's next and known limitations. `planning/PHASE0_PLAN.md`
> has the agreed architecture.

---

## 1. What you need installed

| Tool    | Version                    | Check with        |
|---------|----------------------------|-------------------|
| Python  | 3.13 (3.12 also works)     | `py --version`    |
| Node.js | 24 LTS (22.12 or newer)    | `node --version`  |
| Git     | any recent                 | `git --version`   |

## 2. First-time setup (Windows PowerShell)

Run these inside the `invoicing_io` folder:

```powershell
# Python environment
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
#   If PowerShell blocks the script, run this once, then activate again:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt

# Frontend packages and first build
npm install
npm run build

# Local configuration
copy .env.example .env
$key = python -c "import secrets; print(secrets.token_urlsafe(50))"
(Get-Content .env) -replace '^SECRET_KEY=.*$', "SECRET_KEY=$key" | Set-Content .env -Encoding ascii
Select-String '^SECRET_KEY=.{40,}' .env   # should print the SECRET_KEY line

# Database and your login
python manage.py migrate
python manage.py createsuperuser

# Start
python manage.py runserver
```

Open http://127.0.0.1:8000, sign in, then:

1. **Settings**: enter your business details. Invoices can't be issued until the business name and phone number are set. Under **Payment terms**, set the days to pay and the terms wording for individuals and for institutions.
2. **Products → Add product**: add each item you sell once, with how its stock is counted and the first way you sell it. See **Setting up your products** below.
3. **Customers → Add customer**: add the schools, hospitals and people you supply regularly, and choose **Individual** or **Institution**. One-time buyers don't need saving.
4. **Invoices → New invoice**: choose the customer, add lines (press Enter to move along), then **Save and review**. Check the review page and press **Issue invoice**: it gets its number and stock is deducted. Choosing the customer fills in their type (Individual or Institution), which picks the prices and payment terms; you can change the type, a price or the terms yourself. Press **WhatsApp** or **Email** on an issued invoice to send it (see *Sending invoices* below), or use **View / print image** or **Download**: every invoice is one PNG picture, with payment details at the bottom. A long invoice makes a taller picture rather than a second page.
5. **Payments**: when money comes in, press **Record payment** on the invoice (or on the customer's page, or under Payments). One payment can be split across several of the customer's invoices; type the amount and it fills the oldest first. Anything left over is kept as **credit** and can be applied later (the invoice page offers a one-click "Use credit" button). Each payment has a printable **Receipt**.
   A payment entered by mistake, a bounced cheque or a refund is **reversed** with a reason, never deleted.
6. **Dashboard**: invoiced vs received today, this week and this month; what you're owed and what's overdue; recent invoices and payments; best sellers this month; low stock.
   Left something off an issued invoice? Press **Amend** on it, make the change, say what you changed, and save. It keeps its number, the previous version is kept in its history, and stock is adjusted to match. To call an invoice off entirely, use **Cancel this invoice** (you can then create a corrected replacement).

Then start version control (once):

```powershell
git init
git add .
git commit -m "Phase 1 foundation"
```

`package-lock.json` pins every frontend package version so installs are
repeatable. Keep it committed.

## Setting up your products

Each product has **one stock**, counted in one unit, and one or more
**ways to sell it**, each with its own price. Example values below are
illustrations only; use your own.

**1. Choose what stock is counted in** (when adding the product)
- Liquids: **millilitres (ml)**. Powders and pastes: **grams (g)**.
  Stock is then *shown* in litres or kilograms, and you can type litres or
  kilograms on every stock screen.
- Things you only ever count one by one (bar soap, brushes): a count unit
  such as **pieces** or **bottles**.
- Once a product has stock movements or sales, this can't be changed, so
  the history stays correct.

**2. Add the ways you sell it** (the first one when adding the product,
more on the product's page under **Ways to sell → Add**)
- **Fixed pack or item**: e.g. a "5 L container" that holds 5 L, priced per
  container; or a "500 ml bottle" priced per bottle. On an invoice you type
  the number of packs, and stock goes down by packs × what each holds.
- **By measure**: e.g. "loose, per litre", priced per L. On an invoice you
  can type the quantity in L or ml (1,500 ml at a per-litre price is charged
  as 1.5 L).
- **Institution price** (optional): what schools, hospitals and other
  institutions pay for this way to sell it. Leave it blank and they pay the
  normal selling price.
- A product can have several ways to sell it, all sharing the same stock.
  Mark one as the **default**; it's chosen automatically on new invoices.
- Changing a price only affects new invoices. Untick "Available for new
  invoices" to retire a pack size without losing its history.

**3. Record stock**
- **Stock → Stock in**: choose the product, then type the quantity in L,
  ml or as packs (e.g. 4 × "5 L container" = 20 L). The cost you enter is
  per whatever you typed it in.
- **Stock out**, **Count stock**, **Stock take** and **Customer return** work
  the same way. Stock take asks for each product in litres, kilograms or
  pieces.
- **Low-stock alert** is set in the friendly unit (e.g. alert at 10 L).

**4. Products created before units were added** were converted
automatically the first time you ran `migrate`: a product sold "per litre"
or "per kg" now keeps its stock in ml or g (same amount, e.g. 50 L), and its
old size and price became its first selling option (e.g. "5 L container"
at the old price). Check each product's **Ways to sell** once and correct
anything that doesn't match how you actually sell it. Old invoices are
unchanged.

## Sending invoices

On an issued invoice, press **WhatsApp** or **Email**.
- On a phone that can share files (when the app is opened over HTTPS, as it
  will be once it's online), the phone's share menu opens with the invoice
  image attached: pick WhatsApp, Gmail or any other app.
- Otherwise (on the PC, or a phone opening the app over Wi-Fi), the image is
  downloaded and WhatsApp or your email app opens with the customer and a short
  message filled in. Attach the downloaded image and send.
- WhatsApp opens the customer's chat when a phone number is saved (numbers
  starting 07 or 01 are treated as Kenyan). The app never sends anything itself.

## 3. Day-to-day development

Use two terminals, both in the project folder with the virtual environment
active:

```powershell
npm run dev                  # terminal 1: rebuilds JS/CSS whenever you save a .ts/.css file
python manage.py runserver   # terminal 2: the web app
```

| Task                                   | Command                                   |
|----------------------------------------|-------------------------------------------|
| Run all tests                          | `python manage.py test`                   |
| Type-check TypeScript                  | `npm run typecheck`                       |
| Production build of JS/CSS             | `npm run build`                           |
| Check migrations are up to date        | `python manage.py makemigrations --check` |
| Apply new database changes after an update | `python manage.py migrate`            |
| Verify stock figures match the ledger  | `python manage.py check_stock`            |
| Data admin (for staff users)           | http://127.0.0.1:8000/admin/              |

## 4. How the project is organised

```
config/       Django settings (all secrets/config from environment), URLs
accounts/     User model, sign-in / sign-out
core/         Business settings, audit log, dashboard, money helpers, protected file serving
products/     Product catalogue and categories
customers/    Customers (individuals and organisations)
inventory/    Stock ledger (stock in / out / count adjustments / sales / reversals)
invoices/     Invoices, lines, numbering, calculations, lifecycle services
payments/     Payment methods, payments, allocations to invoices
templates/    Shared layout and partials
static/ts/    TypeScript sources  → compiled to static/dist/ by Vite
static/css/   Custom styles on top of Bootstrap
planning/     Plan, decisions and progress log
```

Business rules live in each app's `services.py` (and `invoices/calculations.py`),
not in views or templates.

## 5. Rules the code enforces

**Money**
- Money is always `Decimal`, never float, and is rounded half-up to cents.
- Totals are always recalculated on the server. Values sent by the browser are only a preview.

**Invoices**
- A draft can be edited freely.
- Issuing an invoice assigns its number (e.g. `INV-2026-0001`), freezes the seller and customer details, and deducts stock. This happens as a single all-or-nothing step.
- Issued invoices can't be deleted or silently changed. To correct one, you **amend** it: the reason is required, every earlier version is kept (viewable and printable, marked SUPERSEDED), the number stays the same, and the stock taken by the old version is returned before the new version's stock is taken, all in one step. An amendment can't bring the total below what's already been paid.
- To call an invoice off, you **cancel** it (a reason is required); a **replacement** can then be created, which links back to the original.
- Each invoice line keeps its own copy of what was sold: the selling option's name, the quantity and unit typed, what the price was per, the conversion used and the stock taken. Changing a product later never changes an issued invoice.

**Units**
- Volume (ml, L) and weight (g, kg) convert exactly: 1 L = 1,000 ml, 1 kg = 1,000 g. Litres never convert to kilograms.
- Count units (piece, bottle, container, carton, pack…) don't convert to each other. "A carton holds 12 bottles" belongs to a product, as a selling option.
- A pack is always charged at its own price, never worked out from a per-litre price.

**Stock**
- Every change is a row in an append-only ledger.
- The quantity shown for a product is the running total of that ledger. `check_stock` verifies this.
- Stock leaves when an invoice is issued, never at the draft stage.
- Cancelling an invoice returns exactly what was deducted, once.
- Database constraints make a double deduction or a double return impossible.
- By default, an invoice can't be issued if it would take stock below zero. This can be changed in Settings.

**Payments**
- Payments are reversed (with a reason), never edited or deleted. A reversed payment stops counting towards its invoices.
- An M-Pesa or bank reference can't be recorded twice for the same method (unless the first was reversed). The same form can't be saved twice.
- One payment can be split across several invoices. Each invoice gets at most its balance; whatever isn't applied stays as credit on the payment.
- Cancelling an invoice turns its payments into credit; issuing its replacement applies that credit to it automatically.
- An amended invoice's total can't drop below what's already been paid.

**Files**
- Uploaded files are served only to signed-in users.
- Uploads are checked to be real PNG, JPEG or WebP images within the size limit, and are stored under random names.

## 6. Configuration

Everything deployment-specific comes from environment variables. For local
development they're read from `.env`. See `.env.example` for the full list
(`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`, `MAX_UPLOAD_MB`, …).
Never commit `.env`.

Production deployment, PostgreSQL, backups and restore steps are covered in
Phase 7 and will be documented here.
