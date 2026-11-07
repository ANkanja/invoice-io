# Plan: unit-aware products, pricing and stock + amending issued invoices

Status: **proposal, waiting for approval.** Written 27 Oct 2026, after looking at the current code and your database.

## 1. What your data shows today

| Record | As stored | Problem |
|---|---|---|
| Product **Soap** | size "5 L", unit "litre", price 500, stock 50, alert at 30 | It's unclear whether 500 is per litre or per 5 L container, and whether 50 means litres or containers. |
| Product **Jik** | size "1L", unit "litre", price 1,300, stock 23, alert at 23 | Same question. |
| Invoice INV-2026-0001 | "Tile Cleaner, 500 ml × KES 0.90"; "HCL, 2 litres × KES 150" | You worked around the missing feature by pricing per ml by hand. |

The app treats every quantity as a plain number of "units", so it can't tell 1 L from 1 ml, can't price per litre while you type ml, and can't sell 500 ml bottles out of a 20 L drum.

## 2. How the unit system will work

### Units (new table, seeded once, extendable)
| Dimension | Units (conversion to the dimension's base) |
|---|---|
| Volume | ml = 1, L = 1,000 |
| Mass | g = 1, kg = 1,000 |
| Count | piece, bottle, container, carton, pack (each converts only to itself) |

- You can add units later in Data admin (e.g. sachet, jerrycan, drum).
- Conversions only happen within a dimension. L → kg is refused, because it depends on density. Different count units (carton → bottle) are never converted automatically either; that conversion is set per product as packaging (below).

### Products get a stock unit plus "ways to sell"
- **Stock unit** (base unit): what stock is counted in, e.g. **ml** for liquid soap, **g** for powder, **piece** for bar soap.
  - Stock is stored in that unit and shown in a friendly one: 18,500 ml shows as **18.5 L**.
- **Selling options** (one or more per product), each either:
  - **Package price:** e.g. "500 ml bottle", which contains 500 ml and sells for KES 140 per bottle. Selling 3 deducts 1,500 ml. The price is never pro-rated, so a 500 ml bottle stays KES 140 even if 1 L is KES 250.
  - **Measured price:** e.g. "Loose, per litre" at KES 250 per L. You can type 1,500 **ml** or 1.5 **L**; both give KES 375.
- One option is the default and pre-fills on invoices.
- Packaging such as "carton of 12 × 500 ml" is just another package option with its own content (6,000 ml) and price. Nothing assumes a fixed carton size.
- **One stock pool per product.** Selling 1 L bottles, 500 ml bottles or loose litres of the same liquid soap all draw from the same ml stock.
  - If you'd rather count pre-filled bottles separately, give that product the stock unit "bottle". Both ways work, chosen per product.

### Invoice lines remember everything (historical snapshot)
Each line stores:
- product and description
- **entered quantity and unit** (e.g. 1,500 ml)
- **pricing method and unit** (per L / per bottle)
- **conversion factor** (ml → L = 0.001)
- quantity in pricing units (1.5 L) and **stock quantity in base units** (1,500 ml)
- effective unit price and the line total

Later changes to a product's units, packaging or prices never alter an issued invoice. The PDF shows, for example, "Liquid soap, 2.5 L × KES 250 per L" or "Liquid soap, 1 L bottle, 5 bottles × KES 250".

### Arithmetic (server is the authority)
- Line amount = entered quantity × (entered unit ÷ pricing unit) × price. This is computed as an exact fraction and rounded once, half up, to the cent.
  - 1,500 ml at KES 250/L → 1.5 × 250 = **KES 375**, never 1,500 × 250.
- The browser preview uses the same exact-fraction method (BigInt), so the preview and the saved total always agree. The server still recalculates on every save.

### Stock
- Every movement is stored in base units, and the form also keeps what you typed ("received 4 × 5 L container" becomes +20,000 ml).
- Stock in / out / count forms let you pick any compatible unit or a package option.
- New movement type **Customer return** (+ stock, with an optional invoice link) for partial returns. "Wasted" is the existing stock-out reasons (damaged, expired…).
- All existing protections stay:
  - negative stock is blocked unless allowed in Settings
  - a deduction can happen only once per line
  - cancelling an invoice reverses exactly what was deducted
  - the ledger stays append-only
  - `check_stock` still verifies everything

## 3. Amending issued invoices ("in case of omissions")

Right now an issued invoice can only be cancelled and replaced, which gives the replacement a **new number**. That is the safest accounting practice, but it's clumsy for an omitted item.

**Proposal: Amend with history.** An issued invoice gets an **Amend** button that opens the normal editor.

On save, in one all-or-nothing step:
1. The current version (lines, totals, customer, dates) is archived as **revision N**. It stays viewable, and its PDF can still be downloaded, marked "superseded".
2. Stock from the old lines is returned and stock for the new lines is deducted, so your stock is always correct.
3. The invoice **keeps its number**. The PDF says "Revised 28 Oct 2026 (revision 2)" so nobody confuses it with the old copy.
4. A short reason is required ("Added omitted 5 L soap"). It's logged with who and when.

Guardrails:
- Cancelled invoices can't be amended.
- Once payments exist (Phase 4), an amendment can't bring the total below what's already paid.
- Amending is its own permission, so a future staff account could be allowed to issue invoices but not amend them.

Cancel and replace stays available for real cancellations (wrong customer, order called off).

## 4. Moving your existing data (needs your answer)

The database changes only **add** things; nothing is dropped.

| Item | Mapping |
|---|---|
| Soap, Jik | Depends on your answers (see the questions). Each becomes a product with stock unit ml and one or two selling options, and its stock is converted to ml with a recorded "unit conversion" adjustment, so the ledger still adds up. |
| INV-2026-0001 | Unchanged. Its free-text lines keep "2 litres × 150" and "500 ml × 0.90" exactly as issued. No stock is involved. |
| Stock history (2 stock-ins) | Converted alongside their product, and the original figures are kept in the note. |

If you'd prefer, I can leave those two products for you to re-create in the new format. Either way, nothing is deleted.

## 5. Build order and tests

1. Units table and conversion service, with tests: 1 L = 1,000 ml, 1 kg = 1,000 g, incompatible units refused.
2. Product stock unit and selling options, the data migration, the product screens, and stock forms with unit choice.
3. Pricing service and invoice line snapshot, with tests:
   - 1,500 ml at KES 250/L = KES 375
   - 5 × 1 L bottles at KES 250 = KES 1,250
   - 5 × 500 ml bottles at KES 140 = KES 700, with no pro-rating
   - fractional quantities and money in Decimal
4. Invoice editor:
   - product, then selling option, then quantity and unit
   - shows the pricing unit
   - live converted totals that match the server
   - clear errors for incompatible units
5. Stock flows, with tests:
   - 3 × 500 ml from 20 L leaves 18.5 L
   - no double deduction
   - cancellation and returns stay accurate
   - issued invoices keep their units, factors and prices
6. Amending issued invoices, with tests: revision archive, stock difference correct, number kept, PDF marked.
7. Update the README with "how to set up your products": stock unit, packages, measured prices, opening stock.

Phase 4 (payments and dashboard) follows once this is in.
