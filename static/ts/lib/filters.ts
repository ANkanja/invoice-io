/**
 * Filter dropdowns marked data-auto-submit apply as soon as they change, so
 * there's no extra tap on a phone. Changing a filter returns to page 1.
 */
export function initAutoSubmit(root: ParentNode = document): void {
  root.querySelectorAll<HTMLSelectElement>("select[data-auto-submit]").forEach((select) => {
    select.addEventListener("change", () => {
      const form = select.form;
      if (!form) return;
      form.querySelector<HTMLInputElement>("input[name=page]")?.remove();
      form.requestSubmit();
    });
  });
}

/**
 * A checkbox with data-controls="some-id" shows/hides that element, e.g.
 * "Track stock" reveals the stock fields. Hidden fields keep their values;
 * the server decides what to keep.
 */
export function initToggles(root: ParentNode = document): void {
  root.querySelectorAll<HTMLInputElement>("input[type=checkbox][data-controls]").forEach((box) => {
    const target = document.getElementById(box.dataset.controls ?? "");
    if (!target) return;
    const sync = (): void => {
      target.hidden = !box.checked;
    };
    box.addEventListener("change", sync);
    sync();
  });
}
