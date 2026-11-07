/**
 * Stock take: show the difference next to each counted box as you type, and
 * highlight rows that will be adjusted. Counts are in each product's friendly
 * unit (litres, kg, bottles), shown next to the box.
 */
import { type Ratio, add, compare, formatRatio, parseDecimal } from "./lib/decimal";

function init(): void {
  document.querySelectorAll<HTMLInputElement>("input.count-input").forEach((input) => {
    // Two places show the difference: its own column (wider screens) and a line
    // under the box (phones).
    const cells = Array.from(document.querySelectorAll<HTMLElement>(`[data-diff-for="${input.id}"]`));
    const row = input.closest("tr");
    const unit = input.closest(".input-group")?.querySelector(".input-group-text")?.textContent?.trim() ?? "";
    const expectedText = input.dataset.expected ?? "0";
    const negative = expectedText.startsWith("-");
    const parsed = parseDecimal(expectedText.replace(/^-/, ""), 6) ?? { num: 0n, den: 1n };
    const expected: Ratio = negative ? { num: -parsed.num, den: parsed.den } : parsed;
    if (cells.length === 0) return;
    const show = (text: string, inline = text): void => {
      cells.forEach((c) => (c.textContent = c.classList.contains("diff-inline") ? inline : text));
    };

    const update = (): void => {
      row?.classList.remove("is-diff", "is-match");
      if (input.value.trim() === "") {
        show("—", "");
        return;
      }
      const counted = parseDecimal(input.value);
      if (counted === null) {
        show("?");
        return;
      }
      const diff = add(counted, { num: -expected.num, den: expected.den });
      const sign = compare(diff, { num: 0n, den: 1n });
      const abs = sign < 0 ? { num: -diff.num, den: diff.den } : diff;
      show(sign === 0 ? "matches" : `${sign > 0 ? "+" : "−"}${formatRatio(abs)} ${unit}`.trim());
      row?.classList.add(sign === 0 ? "is-match" : "is-diff");
    };
    input.addEventListener("input", update);
    update();
  });
}

init();
