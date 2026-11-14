/**
 * Restock plan: live totals while you change quantities and prices.
 *
 * Line total = quantity × cost per unit, rounded half-up to the cent, exactly
 * as the server works it out (restock.models.RestockLine.planned_total), using
 * BigInt fractions. Lines ticked "Remove from plan" don't count. The server
 * recalculates everything on save.
 */
import { centsFor, parseDecimal } from "./lib/decimal";
import { formatCents, parseHundredths } from "./lib/money";

function init(): void {
  const form = document.querySelector<HTMLFormElement>("form[data-restock-form]");
  if (!form) return;
  const total = form.querySelector<HTMLElement>("[data-plan-total]");
  const gap = form.querySelector<HTMLElement>("[data-budget-gap]");
  const budgetInput = form.querySelector<HTMLInputElement>("[data-plan-budget]");
  let dirty = false;

  function recalc(): void {
    let sum = 0n;
    form!.querySelectorAll<HTMLElement>("[data-line]").forEach((line) => {
      const qty = parseDecimal(line.querySelector<HTMLInputElement>("[data-line-qty]")?.value ?? "", 3);
      const cost = parseHundredths(line.querySelector<HTMLInputElement>("[data-line-cost]")?.value ?? "");
      const removed = line.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked ?? false;
      const out = line.querySelector<HTMLElement>("[data-line-total]");
      const cents = qty && cost !== null && qty.num >= 0n && cost >= 0n ? centsFor(qty, cost) : 0n;
      if (out) out.textContent = formatCents(cents);
      line.classList.toggle("is-removed", removed);
      if (!removed) sum += cents;
    });
    if (total) total.textContent = formatCents(sum);
    if (gap) {
      const budget = budgetInput?.value ? parseHundredths(budgetInput.value) : null;
      if (budget === null) {
        gap.textContent = "";
      } else if (sum > budget) {
        gap.textContent = `The plan is ${formatCents(sum - budget)} over the budget.`;
        gap.className = "small mb-0 text-danger";
      } else {
        gap.textContent = `The plan fits the budget, with ${formatCents(budget - sum)} to spare.`;
        gap.className = "small mb-0 text-secondary";
      }
    }
  }

  form.addEventListener("input", () => {
    dirty = true;
    recalc();
  });
  form.addEventListener("change", recalc);
  form.addEventListener("submit", () => (dirty = false));
  window.addEventListener("beforeunload", (event) => {
    if (dirty) event.preventDefault();
  });
  recalc();
}

init();
