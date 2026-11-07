/**
 * Exact money arithmetic for on-screen previews: amounts are BigInt cents
 * (and quantities BigInt hundredths), so there are no floating-point errors.
 * Rounding matches the server: half up, to the cent. The server recalculates
 * everything on save; these numbers are only a preview.
 */

/** "1,234.5" / "12" / "0.05" → hundredths as BigInt. Returns null if invalid or >2 decimals. */
export function parseHundredths(value: string): bigint | null {
  const text = value.trim().replace(/,/g, "");
  if (!/^-?\d+(\.\d{0,2})?$/.test(text)) return null;
  const negative = text.startsWith("-");
  const [whole = "0", frac = ""] = text.replace("-", "").split(".");
  const n = BigInt(whole) * 100n + BigInt(frac.padEnd(2, "0") || "0");
  return negative ? -n : n;
}

/** Integer division rounded half up (away from zero for .5), like Decimal ROUND_HALF_UP. */
export function divRoundHalfUp(numerator: bigint, denominator: bigint): bigint {
  const negative = numerator < 0n !== denominator < 0n;
  const n = numerator < 0n ? -numerator : numerator;
  const d = denominator < 0n ? -denominator : denominator;
  const q = (n * 2n + d) / (d * 2n);
  return negative ? -q : q;
}

/** quantity (hundredths) × price (cents) → cents, rounded half up. */
export function lineGross(quantityHundredths: bigint, priceCents: bigint): bigint {
  return divRoundHalfUp(quantityHundredths * priceCents, 100n);
}

export function formatCents(cents: bigint, currency = "KES"): string {
  const negative = cents < 0n;
  const abs = negative ? -cents : cents;
  const whole = (abs / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const frac = (abs % 100n).toString().padStart(2, "0");
  return `${currency} ${negative ? "-" : ""}${whole}.${frac}`;
}
