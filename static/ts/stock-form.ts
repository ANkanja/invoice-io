/**
 * Stock in / out / count / return forms.
 *
 * - "Counted in" lists the units and packs the chosen product can be entered
 *   in (litres, ml, "5 L container"…), starting in the friendly unit.
 * - Shows current stock and what it will become, converted exactly, and warns
 *   (without blocking) if a stock-out is more than what's recorded.
 * The server does the real conversion and enforces the rules.
 */
import { type Ratio, ZERO, add, compare, mul, parseDecimal } from "./lib/decimal";
import { type UnitTable, formatStock } from "./lib/units";

interface ProductInfo {
  stock: string; // in stock units, may be negative
  stockUnit: string;
  stockDisplay: string;
  default: string; // e.g. "unit:l"
  choices: [string, string, string][]; // value, label, factor to stock units
}

interface StockData {
  products: Record<string, ProductInfo>;
  units: UnitTable;
}

function readData(): StockData | null {
  const el = document.getElementById("stock-product-data");
  if (!el?.textContent) return null;
  try {
    return JSON.parse(el.textContent) as StockData;
  } catch {
    return null;
  }
}

function signed(text: string): Ratio | null {
  const negative = text.trim().startsWith("-");
  const value = parseDecimal(text.trim().replace(/^-/, ""));
  if (!value) return null;
  return negative ? { num: -value.num, den: value.den } : value;
}

function init(): void {
  const select = document.querySelector<HTMLSelectElement>("select[data-stock-product]");
  const measure = document.querySelector<HTMLSelectElement>("select[data-stock-measure]");
  const hint = document.getElementById("stock-hint");
  const data = readData();
  if (!select || !hint || !data) return;
  setup(select, measure, hint, data);
}

function setup(select: HTMLSelectElement, measure: HTMLSelectElement | null, hint: HTMLElement, data: StockData): void {
  const inQty = document.querySelector<HTMLInputElement>("input[name=quantity]:not([data-stock-out])");
  const outQty = document.querySelector<HTMLInputElement>("input[data-stock-out]");
  const counted = document.querySelector<HTMLInputElement>("input[data-stock-count]");

  const fillMeasure = (keep: boolean): void => {
    if (!measure) return;
    const info = data.products[select.value];
    const current = keep ? measure.value : "";
    if (!info) {
      measure.replaceChildren();
      return;
    }
    const wanted = info.choices.some(([value]) => value === current) ? current : info.default;
    measure.replaceChildren(...info.choices.map(([value, label]) => new Option(label, value, false, value === wanted)));
  };

  /** Typed quantity converted to stock units, or null. */
  const toBase = (input: HTMLInputElement | null): Ratio | null => {
    const info = data.products[select.value];
    const qty = parseDecimal(input?.value ?? "");
    if (!info || !qty) return null;
    const choice = info.choices.find(([value]) => value === measure?.value) ?? info.choices.find(([v]) => v === info.default);
    const factor = choice ? parseDecimal(choice[2]) : null;
    return factor ? mul(qty, factor) : null;
  };

  const update = (): void => {
    const info = data.products[select.value];
    if (!info) {
      hint.hidden = true;
      return;
    }
    const current = signed(info.stock) ?? ZERO;
    const show = (value: Ratio): string => {
      const negative = value.num < 0n;
      const text = formatStock(data.units, negative ? { num: -value.num, den: value.den } : value, info.stockUnit);
      return negative ? `minus ${text}` : text;
    };
    let text = `In stock now: ${info.stockDisplay}.`;
    hint.classList.remove("is-warning");

    if (inQty?.value) {
      const added = toBase(inQty);
      if (added && added.num > 0n) text += ` After this: ${show(add(current, added))}.`;
    } else if (outQty?.value) {
      const removed = toBase(outQty);
      if (removed && removed.num > 0n) {
        const after = add(current, { num: -removed.num, den: removed.den });
        text += ` After this: ${show(after)}.`;
        if (compare(after, ZERO) < 0) {
          text += " That's more than is recorded in stock.";
          hint.classList.add("is-warning");
        }
      }
    } else if (counted?.value) {
      const c = toBase(counted);
      if (c) {
        const diff = add(c, { num: -current.num, den: current.den });
        const sign = compare(diff, ZERO);
        text += sign === 0 ? " Count matches." : ` Adjustment: ${sign > 0 ? "+" : "-"}${show(sign > 0 ? diff : { num: -diff.num, den: diff.den })}.`;
      }
    }
    hint.textContent = text;
    hint.hidden = false;
  };

  select.addEventListener("change", () => {
    fillMeasure(false);
    update();
  });
  measure?.addEventListener("change", update);
  [inQty, outQty, counted].forEach((el) => el?.addEventListener("input", update));
  // On first load keep what the server rendered (e.g. after a validation error).
  if (measure && measure.options.length === 0) fillMeasure(false);
  update();
}

init();
