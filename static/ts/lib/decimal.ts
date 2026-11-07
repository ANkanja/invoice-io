/**
 * Exact decimal arithmetic for on-screen previews of quantities and unit
 * conversions (1,500 ml → 1.5 L). Values are kept as BigInt fractions, so
 * nothing is lost to floating point; rounding to cents is half up, like the
 * server. The server recalculates everything on save.
 */
import { divRoundHalfUp } from "./money";

/** A non-negative decimal: value = num / den. */
export interface Ratio {
  num: bigint;
  den: bigint;
}

const TEN = 10n;

/** "1,500" / "2.25" / "1000.000" → Ratio. Null if not a plain number or more than `maxDecimals` places. */
export function parseDecimal(value: string, maxDecimals = 3): Ratio | null {
  const text = value.trim().replace(/,/g, "");
  const match = /^(\d+)(?:\.(\d*))?$/.exec(text);
  if (!match) return null;
  const whole = match[1] ?? "0";
  const frac = (match[2] ?? "").replace(/0+$/, "");
  if (frac.length > maxDecimals) return null;
  const den = TEN ** BigInt(frac.length);
  return { num: BigInt(whole) * den + BigInt(frac || "0"), den };
}

export function mul(a: Ratio, b: Ratio): Ratio {
  return { num: a.num * b.num, den: a.den * b.den };
}

export function div(a: Ratio, b: Ratio): Ratio {
  return { num: a.num * b.den, den: a.den * b.num };
}

export function add(a: Ratio, b: Ratio): Ratio {
  return { num: a.num * b.den + b.num * a.den, den: a.den * b.den };
}

export function compare(a: Ratio, b: Ratio): number {
  const left = a.num * b.den;
  const right = b.num * a.den;
  return left === right ? 0 : left < right ? -1 : 1;
}

export const ZERO: Ratio = { num: 0n, den: 1n };

/** quantity × price in cents → cents, rounded half up (same as the server). */
export function centsFor(quantity: Ratio, priceCents: bigint): bigint {
  return divRoundHalfUp(quantity.num * priceCents, quantity.den);
}

/** Readable number: thousands separators, up to `maxDecimals` places, trailing zeros dropped. */
export function formatRatio(value: Ratio, maxDecimals = 3): string {
  const scale = TEN ** BigInt(maxDecimals);
  const negative = value.num < 0n !== value.den < 0n;
  const absNum = value.num < 0n ? -value.num : value.num;
  const absDen = value.den < 0n ? -value.den : value.den;
  const scaled = divRoundHalfUp(absNum * scale, absDen);
  const whole = (scaled / scale).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const frac = (scaled % scale).toString().padStart(maxDecimals, "0").replace(/0+$/, "");
  return `${negative && scaled !== 0n ? "-" : ""}${whole}${frac ? `.${frac}` : ""}`;
}
