/**
 * Display-only quantity helpers. Quantities have at most 2 decimals, so we work
 * in integer hundredths to avoid floating-point surprises (0.1 + 0.2).
 * The server does all real stock arithmetic with Decimal.
 */
export function toHundredths(value: string): number | null {
  const text = value.trim();
  if (!/^-?\d+(\.\d{1,2})?$/.test(text)) return null;
  const negative = text.startsWith("-");
  const [whole = "0", frac = ""] = text.replace("-", "").split(".");
  const n = Number(whole) * 100 + Number(frac.padEnd(2, "0"));
  return negative ? -n : n;
}

export function formatHundredths(n: number): string {
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  const whole = Math.floor(abs / 100).toLocaleString("en-GB");
  const frac = abs % 100;
  if (frac === 0) return `${sign}${whole}`;
  return `${sign}${whole}.${String(frac).padStart(2, "0").replace(/0$/, "")}`;
}
