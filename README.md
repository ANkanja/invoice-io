# invoicing_io

A private web app for running a soap and cleaning-products business: products,
stock, customers, invoices (with PDF), payments, and template-based promotional
posters.

Built with Django 5.2 LTS, server-rendered templates, Bootstrap 5.3 and
TypeScript (compiled by Vite). SQLite is used for local development and
PostgreSQL in production.

> **Status:** Phase 1 (foundation) is complete. See `planning/PROGRESS.md` for
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
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
#   Paste the printed value after SECRET_KEY= in .env

# Database and your login
python manage.py migrate
python manage.py createsuperuser

# Start
python manage.py runserver
```

Open http://127.0.0.1:8000, sign in, then go to **Settings** and enter your
business details. Invoices can't be issued until the business name and phone
number are set.

Then start version control (once):

```powershell
git init
git add .
git commit -m "Phase 1 foundation"
```

`package-lock.json` pins every frontend package version so installs are
repeatable. Keep it committed.

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
- Issued invoices can't be edited or deleted. To correct one, you **cancel** it (a reason is required) and then issue a **replacement**, which links back to the original.

**Stock**
- Every change is a row in an append-only ledger.
- The quantity shown for a product is the running total of that ledger. `check_stock` verifies this.
- Stock leaves when an invoice is issued, never at the draft stage.
- Cancelling an invoice returns exactly what was deducted, once.
- Database constraints make a double deduction or a double return impossible.
- By default, an invoice can't be issued if it would take stock below zero. This can be changed in Settings.

**Payments**
- Payments are reversed, never edited or deleted.
- An M-Pesa or bank reference can't be recorded twice.
- One payment can be split across several invoices.

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
