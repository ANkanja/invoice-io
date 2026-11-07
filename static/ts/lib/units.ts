/**
 * Unit data sent by the server (products.models.Unit) and helpers that mirror
 * products/units.py: same-dimension conversions only, and stock shown in the
 * friendly unit (litres for ml, kilograms for g).
 */
import { type Ratio, div, formatRatio, mul, parseDecimal } from "./decimal";

export interface UnitInfo {
  symbol: string;
  name: string;
  plural: string;
  dimension: "volume" | "mass" | "count";
  factor: string; // size in ml or g; count units are 1
}

export type UnitTable = Record<string, UnitInfo>;

export function factorOf(units: UnitTable, code: string): Ratio | null {
  const unit = units[code];
  return unit ? parseDecimal(unit.factor) : null;
}

/** Multiply a quantity in `from` by this to get it in `to`. Null if incompatible. */
export function conversion(units: UnitTable, from: string, to: string): Ratio | null {
  const a = units[from];
  const b = units[to];
  if (!a || !b || a.dimension !== b.dimension) return null;
  if (a.dimension === "count") return from === to ? { num: 1n, den: 1n } : null;
  const fa = factorOf(units, from);
  const fb = factorOf(units, to);
  return fa && fb ? div(fa, fb) : null;
}

/** The next larger unit of the same kind (ml → L, g → kg), or the unit itself. */
export function displayUnitFor(units: UnitTable, stockCode: string): string {
  const stock = units[stockCode];
  if (!stock || stock.dimension === "count") return stockCode;
  const own = parseDecimal(stock.factor);
  let best: string | null = null;
  let bestFactor: Ratio | null = null;
  for (const [code, unit] of Object.entries(units)) {
    if (unit.dimension !== stock.dimension) continue;
    const f = parseDecimal(unit.factor);
    if (!f || !own || f.num * own.den <= own.num * f.den) continue;
    if (!bestFactor || f.num * bestFactor.den < bestFactor.num * f.den) {
      best = code;
      bestFactor = f;
    }
  }
  return best ?? stockCode;
}

/** "1 bottle", "3 bottles", "1.5 L". */
export function formatAmount(units: UnitTable, quantity: Ratio, code: string): string {
  const unit = units[code];
  const text = formatRatio(quantity);
  if (!unit) return text;
  if (unit.dimension === "count") return `${text} ${quantity.num === quantity.den ? unit.name : unit.plural}`;
  return `${text} ${unit.symbol}`;
}

/** A quantity in stock units shown in the friendly unit: 18,500 ml → "18.5 L". */
export function formatStock(units: UnitTable, base: Ratio, stockCode: string): string {
  const display = displayUnitFor(units, stockCode);
  const factor = conversion(units, stockCode, display);
  return formatAmount(units, factor ? mul(base, factor) : base, factor ? display : stockCode);
}
