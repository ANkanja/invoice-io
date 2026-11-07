/**
 * Record payment form.
 *
 * - Choosing a different customer reloads the page with their unpaid invoices.
 * - Typing the amount spreads it over the invoices: the invoice you came from
 *   first, then oldest first, never more than each one's balance. Once you
 *   edit an invoice box yourself, your figures are kept ("Fill oldest first"
 *   redoes it).
 * - Shows how much is applied and how much stays as credit. Exact cents
 *   (BigInt); the server checks everything again.
 */
import { formatCents, parseHundredths } from "./lib/money";

interface MethodInfo {
  needsRef: boolean;
}

function readMethods(): Record<string, MethodInfo> {
  const el = document.getElementById("payment-methods");
  try {
    return el?.textContent ? (JSON.parse(el.textContent) as Record<string, MethodInfo>) : {};
  } catch {
    return {};
  }
}

function init(): void {
  const form = document.querySelector<HTMLFormElement>("form[data-payment-form]");
  if (!form) return;
  setup(form);
}

function setup(form: HTMLFormElement): void {
  const amount = form.querySelector<HTMLInputElement>("[data-payment-amount]");
  const customer = form.querySelector<HTMLSelectElement>("[data-payment-customer]");
  const method = form.querySelector<HTMLSelectElement>("[data-payment-method]");
  const reference = form.querySelector<HTMLInputElement>("input[name=reference]");
  const appliedOut = form.querySelector<HTMLElement>("[data-applied]");
  const leftOut = form.querySelector<HTMLElement>("[data-left]");
  const note = form.querySelector<HTMLElement>("[data-alloc-note]");
  const methods = readMethods();
  const rows = Array.from(form.querySelectorAll<HTMLTableRowElement>(".alloc-table tbody tr"));
  // The invoice the form was opened from goes first, then the table order (oldest first).
  rows.sort((a, b) => Number(b.hasAttribute("data-preferred")) - Number(a.hasAttribute("data-preferred")));
  const inputs = rows
    .map((row) => row.querySelector<HTMLInputElement>("input[data-alloc]"))
    .filter((el): el is HTMLInputElement => el !== null);
  let manual = false;

  const cents = (text: string): bigint => parseHundredths(text || "0") ?? 0n;
  const toText = (c: bigint): string => {
    const whole = c / 100n;
    const frac = (c % 100n).toString().padStart(2, "0");
    return `${whole}.${frac}`;
  };

  function autoFill(): void {
    let left = cents(amount?.value ?? "");
    for (const input of inputs) {
      const balance = cents(input.dataset.balance ?? "0");
      const take = left > balance ? balance : left > 0n ? left : 0n;
      input.value = take > 0n ? toText(take) : "";
      left -= take;
    }
  }

  function summary(): void {
    if (!appliedOut || !leftOut) return;
    const total = cents(amount?.value ?? "");
    let applied = 0n;
    let invalid = false;
    for (const input of inputs) {
      const value = parseHundredths(input.value || "0");
      const balance = cents(input.dataset.balance ?? "0");
      const tooMuch = value === null || value < 0n || value > balance;
      input.classList.toggle("is-invalid", tooMuch);
      if (tooMuch) invalid = true;
      else applied += value ?? 0n;
    }
    appliedOut.textContent = formatCents(applied);
    const left = total - applied;
    leftOut.textContent = left < 0n ? "more than received" : formatCents(left);
    leftOut.classList.toggle("text-danger", left < 0n);
    if (note) {
      note.textContent = invalid
        ? "An amount is more than that invoice's balance."
        : left > 0n
          ? "The rest stays as credit on this payment; you can apply it later."
          : "";
    }
  }

  function syncReference(): void {
    const needs = method ? methods[method.value]?.needsRef ?? false : false;
    const label = reference?.labels?.[0];
    if (!label) return;
    label.querySelector(".text-danger")?.remove();
    if (needs) {
      const star = document.createElement("span");
      star.className = "text-danger";
      star.setAttribute("aria-hidden", "true");
      star.textContent = " *";
      label.append(star);
    }
    reference?.toggleAttribute("required", needs);
  }

  // Treat what's on screen as typed by hand unless it's exactly what auto-fill
  // would produce (e.g. the form re-shown after an error keeps your figures).
  const shown = inputs.map((el) => el.value);
  autoFill();
  manual = inputs.some((el, i) => cents(el.value) !== cents(shown[i] ?? ""));
  if (manual) inputs.forEach((el, i) => (el.value = shown[i] ?? ""));

  amount?.addEventListener("input", () => {
    if (!manual) autoFill();
    summary();
  });
  inputs.forEach((input) =>
    input.addEventListener("input", () => {
      manual = true;
      summary();
    }),
  );
  form.querySelector("[data-auto-fill]")?.addEventListener("click", () => {
    manual = false;
    autoFill();
    summary();
  });
  customer?.addEventListener("change", () => {
    // Reload with this customer's unpaid invoices, keeping what's been typed.
    const params = new URLSearchParams(window.location.search);
    params.delete("invoice");
    if (customer.value) params.set("customer", customer.value);
    else params.delete("customer");
    window.location.search = params.toString();
  });
  method?.addEventListener("change", syncReference);
  syncReference();
  summary();
}

init();
