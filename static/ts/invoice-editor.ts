/**
 * Invoice editor (new, draft edit and amend).
 *
 * - Add / remove lines; Django formset field names are renumbered so the
 *   server always receives lines-0 … lines-N in on-screen order.
 * - Choosing a product lists how it's sold ("5 L container", "loose, per L");
 *   choosing one fills its price and the units the quantity can be typed in.
 *   Packs are priced per pack (never pro-rated); measures convert exactly
 *   (1,500 ml at KES 250 per L = KES 375).
 * - Live totals with exact BigInt arithmetic (same rounding as the server, see
 *   products/pricing.py). They are a preview only: the server recalculates
 *   everything on save.
 * - Enter moves to the next box instead of submitting; at the end of the last
 *   line it adds a new line.
 * - Warns before leaving the page with unsaved changes.
 */
import { type Ratio, ZERO, add, centsFor, compare, formatRatio, mul, parseDecimal } from "./lib/decimal";
import { divRoundHalfUp, formatCents, parseHundredths } from "./lib/money";
import { type UnitTable, conversion, formatStock } from "./lib/units";

interface OptionInfo {
  id: number;
  label: string;
  method: "package" | "measure";
  price: string;
  per: string; // "per bottle", "per L"
  isDefault: boolean;
  active: boolean;
  content: string; // package: stock units in one pack
  priceUnit: string; // measure: unit code the price is per
  packUnit: string; // package: unit code of the pack
  units: string[]; // codes the quantity can be entered in
}

interface ProductInfo {
  id: number;
  name: string;
  tracked: boolean;
  active: boolean;
  stockUnit: string;
  stock: string; // available, in stock units
  stockDisplay: string;
  options: OptionInfo[];
}

interface CustomerInfo {
  contact: string;
  phone: string;
  email: string;
  address: string;
}

interface EditorData {
  products: ProductInfo[];
  units: UnitTable;
  customers: Record<string, CustomerInfo>;
  taxRate: string;
  taxLabel: string;
  pricesIncludeTax: boolean;
  currency: string;
  allowNegativeStock: boolean;
}

const PREFIX = "lines";
const FIELD_PATTERN = new RegExp(`${PREFIX}-(\\d+|__prefix__)-`, "g");

function readData(): EditorData | null {
  const el = document.getElementById("invoice-editor-data");
  if (!el?.textContent) return null;
  try {
    return JSON.parse(el.textContent) as EditorData;
  } catch {
    return null;
  }
}

function init(): void {
  const form = document.querySelector<HTMLFormElement>("form[data-invoice-form]");
  const data = readData();
  const container = form?.querySelector<HTMLElement>("[data-lines]");
  const template = document.getElementById("line-template") as HTMLTemplateElement | null;
  const totalForms = document.getElementById(`id_${PREFIX}-TOTAL_FORMS`) as HTMLInputElement | null;
  if (!form || !data || !container || !template || !totalForms) return;
  setup(form, data, container, template, totalForms);
}

function setup(
  form: HTMLFormElement,
  data: EditorData,
  container: HTMLElement,
  template: HTMLTemplateElement,
  totalForms: HTMLInputElement,
): void {

  const products = new Map(data.products.map((p) => [String(p.id), p]));
  const money = (cents: bigint): string => formatCents(cents, data.currency);
  let dirty = false;

  // ---- rows ----------------------------------------------------------------
  const rows = (): HTMLElement[] => Array.from(container.querySelectorAll<HTMLElement>("[data-line]"));
  const field = (row: HTMLElement, name: string): HTMLInputElement | HTMLSelectElement | null =>
    row.querySelector(`[name$="-${name}"]`);

  function renumber(): void {
    rows().forEach((row, index) => {
      row.querySelectorAll<HTMLElement>("[name], [id], label[for]").forEach((el) => {
        for (const attr of ["name", "id", "for"]) {
          const value = el.getAttribute(attr);
          if (value) el.setAttribute(attr, value.replace(FIELD_PATTERN, `${PREFIX}-${index}-`));
        }
      });
    });
    totalForms.value = String(rows().length);
  }

  function addLine(focus = true): HTMLElement {
    const fragment = template.content.cloneNode(true) as DocumentFragment;
    const row = fragment.querySelector<HTMLElement>("[data-line]");
    container.appendChild(fragment);
    renumber();
    const added = rows()[rows().length - 1] ?? row!;
    wireRow(added);
    if (focus) field(added, "product")?.focus();
    return added;
  }

  function removeLine(row: HTMLElement): void {
    if (rows().length <= 1) {
      // Keep one row on screen: clear it instead.
      row.querySelectorAll<HTMLInputElement | HTMLSelectElement>("input, select").forEach((el) => (el.value = ""));
      delete row.dataset.autoDescription;
      applyProduct(row, false);
    } else {
      const next = row.nextElementSibling ?? row.previousElementSibling;
      row.remove();
      renumber();
      (next as HTMLElement | null)?.querySelector<HTMLElement>("select, input")?.focus();
    }
    dirty = true;
    recalc();
  }

  // ---- product, selling option and unit ----------------------------------------------
  const input = (row: HTMLElement, name: string): HTMLInputElement | null =>
    field(row, name) as HTMLInputElement | null;
  const select = (row: HTMLElement, name: string): HTMLSelectElement | null =>
    field(row, name) as HTMLSelectElement | null;
  const productOf = (row: HTMLElement): ProductInfo | undefined => products.get(select(row, "product")?.value ?? "");

  function optionOf(row: HTMLElement): OptionInfo | undefined {
    const product = productOf(row);
    const value = select(row, "selling_option")?.value ?? "";
    return product?.options.find((o) => String(o.id) === value);
  }

  function unitLabel(code: string, option: OptionInfo): string {
    const unit = data.units[code];
    if (!unit) return code;
    return option.method === "package" ? unit.plural : unit.symbol;
  }

  function fillSelect(el: HTMLSelectElement, items: [string, string][], wanted: string): void {
    el.replaceChildren(...items.map(([value, text]) => new Option(text, value, false, value === wanted)));
    if (!items.some(([value]) => value === wanted) && items.length) el.value = items[0]![0];
  }

  function applyProduct(row: HTMLElement, fromUser: boolean): void {
    const product = productOf(row);
    const description = input(row, "description");
    const optionSelect = select(row, "selling_option");
    const qty = input(row, "quantity");
    const optionWrap = row.querySelector<HTMLElement>("[data-option-wrap]");
    const unitSelect = select(row, "entered_unit");
    const freeUnit = input(row, "free_unit");

    if (optionWrap) optionWrap.hidden = !product;
    if (unitSelect) unitSelect.hidden = !product;
    if (freeUnit) freeUnit.hidden = Boolean(product);

    if (optionSelect) {
      if (product) {
        const current = optionSelect.value || optionSelect.dataset.initial || "";
        const shown = product.options.filter((o) => o.active || String(o.id) === current);
        const fallback = shown.find((o) => o.isDefault) ?? shown[0];
        fillSelect(
          optionSelect,
          shown.map((o) => [String(o.id), `${o.label} · ${money(parseHundredths(o.price) ?? 0n)}`]),
          fromUser ? String(fallback?.id ?? "") : current || String(fallback?.id ?? ""),
        );
        optionWrap?.toggleAttribute("hidden", shown.length === 0);
      } else {
        optionSelect.replaceChildren();
      }
      delete optionSelect.dataset.initial;
    }

    if (product && fromUser) {
      // Replace the description only if it was empty or filled in by a previous product.
      if (description && (!description.value || description.value === row.dataset.autoDescription)) {
        description.value = product.name;
        row.dataset.autoDescription = product.name;
      }
      if (qty && !qty.value) qty.value = "1";
    }
    if (description) description.placeholder = product ? product.name : "What are you selling?";
    applyOption(row, fromUser);
  }

  function applyOption(row: HTMLElement, fromUser: boolean): void {
    const option = optionOf(row);
    const unitSelect = select(row, "entered_unit");
    const price = input(row, "unit_price");
    const per = row.querySelector<HTMLElement>("[data-line-per]");
    if (unitSelect) {
      if (option) {
        const current = unitSelect.value || unitSelect.dataset.initial || "";
        const preferred = option.method === "package" ? option.packUnit : option.priceUnit;
        const wanted = option.units.includes(current) ? current : preferred;
        fillSelect(unitSelect, option.units.map((code) => [code, unitLabel(code, option)]), wanted);
      } else {
        unitSelect.replaceChildren();
      }
      delete unitSelect.dataset.initial;
    }
    if (option && fromUser && price) price.value = option.price;
    if (per) {
      const freeUnit = input(row, "free_unit")?.value.trim();
      per.textContent = option ? option.per : freeUnit ? `per ${freeUnit}` : "";
    }
  }

  interface LineQuantities {
    priceQty: Ratio; // in the units the price is per
    base: Ratio | null; // in the product's stock units (null: not stocked)
    conversionNote: string; // "= 1.5 L" when typed in another unit
  }

  /** Mirrors products.pricing.resolve_quantity. Null if the quantity or unit is invalid. */
  function quantitiesOf(row: HTMLElement): LineQuantities | null {
    const qty = parseDecimal(input(row, "quantity")?.value ?? "", 2);
    if (!qty || qty.num <= 0n) return null;
    const product = productOf(row);
    if (!product) return { priceQty: qty, base: null, conversionNote: "" };
    const option = optionOf(row);
    if (!option) return null;
    if (option.method === "package") {
      const content = parseDecimal(option.content);
      return { priceQty: qty, base: content ? mul(qty, content) : null, conversionNote: "" };
    }
    const entered = select(row, "entered_unit")?.value || option.priceUnit;
    const toPrice = conversion(data.units, entered, option.priceUnit);
    const toStock = conversion(data.units, entered, product.stockUnit);
    if (!toPrice || !toStock) return null;
    const priceQty = mul(qty, toPrice);
    const note = entered === option.priceUnit ? "" : `= ${formatRatio(priceQty, 6)} ${data.units[option.priceUnit]?.symbol ?? ""}`;
    return { priceQty, base: mul(qty, toStock), conversionNote: note };
  }

  function updateHint(row: HTMLElement, needed: Map<number, Ratio>, quantities: LineQuantities | null): void {
    const hint = row.querySelector<HTMLElement>("[data-line-hint]");
    if (!hint) return;
    const product = productOf(row);
    hint.className = "line-hint";
    if (!product) {
      hint.textContent = "";
      return;
    }
    const parts: string[] = [];
    if (!product.active) parts.push("Archived product");
    if (quantities?.conversionNote) parts.push(quantities.conversionNote);
    if (!product.options.length) {
      parts.push("No price set up for this product yet");
      hint.classList.add("is-warning");
    }
    if (product.tracked) {
      parts.push(`${product.stockDisplay} in stock`);
      const stock = parseDecimal(product.stock.replace(/^-/, "")) ?? ZERO;
      const available = product.stock.startsWith("-") ? { num: -stock.num, den: stock.den } : stock;
      const total = needed.get(product.id);
      if (total && compare(total, available) > 0) {
        parts.push(`this invoice needs ${formatStock(data.units, total, product.stockUnit)}`);
        parts.push(data.allowNegativeStock ? "stock will go below zero" : "not enough to issue");
        hint.classList.add("is-warning");
      }
    }
    const option = optionOf(row);
    const price = input(row, "unit_price")?.value ?? "";
    if (option && price && parseHundredths(price) !== parseHundredths(option.price)) {
      parts.push(`usual price ${money(parseHundredths(option.price) ?? 0n)} ${option.per}`);
    }
    hint.textContent = parts.join(" · ");
  }

  // ---- totals ------------------------------------------------------------------------
  function setTotal(name: string, text: string, show = true): void {
    document.querySelectorAll<HTMLElement>(`[data-total="${name}"]`).forEach((el) => (el.textContent = text));
    document.querySelectorAll<HTMLElement>(`[data-row="${name}"]`).forEach((el) => (el.hidden = !show));
  }

  function recalc(): void {
    let subtotal = 0n;
    let complete = true;
    const needed = new Map<number, Ratio>();
    const perRow = new Map<HTMLElement, LineQuantities | null>();

    for (const row of rows()) {
      const qtyText = input(row, "quantity")?.value ?? "";
      const priceText = input(row, "unit_price")?.value ?? "";
      const discText = input(row, "line_discount")?.value ?? "";
      const out = row.querySelector<HTMLOutputElement>("[data-line-amount]");
      const blank = !qtyText && !priceText && !select(row, "product")?.value && !input(row, "description")?.value;
      const quantities = blank ? null : quantitiesOf(row);
      perRow.set(row, quantities);
      if (blank) {
        if (out) out.textContent = "—";
        continue;
      }
      const product = productOf(row);
      if (quantities?.base && product?.tracked) {
        needed.set(product.id, add(needed.get(product.id) ?? ZERO, quantities.base));
      }
      const price = parseHundredths(priceText || (optionOf(row)?.price ?? ""));
      const disc = discText ? parseHundredths(discText) : 0n;
      if (!quantities || price === null || disc === null || price < 0n || disc < 0n) {
        if (out) out.textContent = "—";
        complete = false;
        continue;
      }
      const gross = centsFor(quantities.priceQty, price);
      if (disc > gross) {
        if (out) out.textContent = "discount too big";
        complete = false;
        continue;
      }
      const total = gross - disc;
      subtotal += total;
      if (out) out.textContent = money(total);
    }
    for (const [row, quantities] of perRow) updateHint(row, needed, quantities);
    const discInput = form.querySelector<HTMLInputElement>("[data-invoice-discount]");
    const invoiceDiscount = discInput?.value ? parseHundredths(discInput.value) ?? 0n : 0n;
    const afterDiscount = subtotal - invoiceDiscount;
    const rate = parseHundredths(data.taxRate) ?? 0n; // hundredths of a percent, e.g. 16.00 → 1600
    let tax = 0n;
    let total = afterDiscount;
    if (rate > 0n && afterDiscount > 0n) {
      if (data.pricesIncludeTax) {
        tax = divRoundHalfUp(afterDiscount * rate, 10000n + rate);
      } else {
        tax = divRoundHalfUp(afterDiscount * rate, 10000n);
        total = afterDiscount + tax;
      }
    }

    setTotal("subtotal", money(subtotal));
    setTotal("discount", `- ${money(invoiceDiscount)}`, invoiceDiscount > 0n);
    const taxLabel = document.querySelector<HTMLElement>("[data-tax-label]");
    if (taxLabel) taxLabel.textContent = `${data.taxLabel || "Tax"} ${data.taxRate}%${data.pricesIncludeTax ? " (included)" : ""}`;
    setTotal("tax", money(tax), rate > 0n);
    setTotal("total", invoiceDiscount > subtotal ? "discount too big" : money(total));
    const note = document.querySelector<HTMLElement>("[data-totals-note]");
    if (note) {
      note.textContent = complete
        ? "Totals are checked again on the server when you save."
        : "Some lines are incomplete and aren't counted yet.";
    }
  }

  // ---- keyboard: Enter moves on --------------------------------------------------------
  function onKeydown(event: KeyboardEvent): void {
    if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.metaKey) return;
    const target = event.target as HTMLElement;
    const row = target.closest<HTMLElement>("[data-line]");
    if (!row || target.tagName === "BUTTON") return;
    event.preventDefault();
    const inputs = Array.from(row.querySelectorAll<HTMLElement>("select, input:not([type=hidden])"))
      .filter((el) => !el.closest("[hidden]"));
    const index = inputs.indexOf(target);
    const next = inputs[index + 1];
    if (next) {
      next.focus();
      if (next instanceof HTMLInputElement) next.select();
    } else if (row === rows()[rows().length - 1]) {
      addLine();
    } else {
      field(rows()[rows().indexOf(row) + 1]!, "product")?.focus();
    }
  }

  function wireRow(row: HTMLElement): void {
    if (row.dataset.wired) return;
    row.dataset.wired = "1";
    const productSelect = select(row, "product");
    const description = input(row, "description");
    if (productSelect && description && productSelect.value && description.value === products.get(productSelect.value)?.name) {
      row.dataset.autoDescription = description.value;
    }
    productSelect?.addEventListener("change", () => {
      applyProduct(row, true);
      recalc();
    });
    select(row, "selling_option")?.addEventListener("change", () => {
      applyOption(row, true);
      recalc();
    });
    input(row, "free_unit")?.addEventListener("input", () => applyOption(row, false));
    row.querySelector("[data-remove-line]")?.addEventListener("click", () => removeLine(row));
    applyProduct(row, false);
  }

  // ---- customer ------------------------------------------------------------------------------
  const customerSelect = form.querySelector<HTMLSelectElement>("[data-customer-select]");
  const oneTime = form.querySelector<HTMLElement>("[data-one-time-fields]");
  const summary = form.querySelector<HTMLElement>("[data-customer-summary]");
  function syncCustomer(): void {
    if (!customerSelect || !oneTime || !summary) return;
    const info = data.customers[customerSelect.value];
    oneTime.hidden = Boolean(customerSelect.value);
    summary.hidden = !info;
    if (info) {
      summary.replaceChildren(
        ...[info.contact && `Contact: ${info.contact}`, info.phone, info.email, info.address]
          .filter((x): x is string => Boolean(x))
          .map((text) => Object.assign(document.createElement("div"), { textContent: text })),
      );
      if (!summary.childElementCount) summary.textContent = "No contact details saved for this customer.";
    }
  }
  customerSelect?.addEventListener("change", syncCustomer);
  syncCustomer();

  // ---- wire everything --------------------------------------------------------------
  rows().forEach(wireRow);
  form.querySelector("[data-add-line]")?.addEventListener("click", () => addLine());
  form.addEventListener("input", () => {
    dirty = true;
    recalc();
  });
  form.addEventListener("change", () => (dirty = true));
  form.addEventListener("keydown", onKeydown);
  form.addEventListener("submit", () => {
    dirty = false;
    renumber();
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty) event.preventDefault();
  });
  recalc();
}

init();
