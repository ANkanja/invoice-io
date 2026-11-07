/**
 * Product form and selling-option form:
 * - unit dropdowns only offer units compatible with the stock unit
 *   (ml/L for liquids, g/kg for powders, the item itself for counted stock);
 * - pack fields or measured-price fields show depending on "How is it sold?";
 * - the low-stock alert label shows its unit (L, kg, bottles).
 * The server validates all of this again.
 */
interface UnitInfo {
  id: number;
  code: string;
  symbol: string;
  name: string;
  plural: string;
  dimension: "volume" | "mass" | "count";
  factor: string;
}

function readUnits(): UnitInfo[] {
  try {
    return JSON.parse(document.getElementById("units-data")?.textContent ?? "[]") as UnitInfo[];
  } catch {
    return [];
  }
}

function init(): void {
  const form = document.querySelector<HTMLFormElement>("form[data-product-form]");
  if (!form) return;
  const units = readUnits();
  const byId = new Map(units.map((u) => [String(u.id), u]));
  const stockSelect = form.querySelector<HTMLSelectElement>("select[data-stock-unit]");
  const fixedDimension = form.dataset.fixedDimension;

  const stockUnit = (): UnitInfo | undefined => (stockSelect ? byId.get(stockSelect.value) : undefined);

  /** The friendly display unit: litres for ml, kilograms for g, else the unit itself. */
  const displayUnit = (unit: UnitInfo): UnitInfo => {
    if (unit.dimension === "count") return unit;
    const larger = units
      .filter((u) => u.dimension === unit.dimension && Number(u.factor) > Number(unit.factor))
      .sort((a, b) => Number(a.factor) - Number(b.factor));
    return larger[0] ?? unit;
  };

  function filterUnitSelects(): void {
    const stock = stockUnit();
    const dimension = stock?.dimension ?? fixedDimension;
    if (!dimension) return;
    form!.querySelectorAll<HTMLSelectElement>("select[data-dimension-filter]").forEach((select) => {
      let firstAllowed: HTMLOptionElement | undefined;
      let preferred: HTMLOptionElement | undefined;
      for (const opt of Array.from(select.options)) {
        const unit = byId.get(opt.value);
        const allowed = unit !== undefined && unit.dimension === dimension &&
          (dimension !== "count" || !stock || unit.id === stock.id);
        opt.hidden = !allowed;
        opt.disabled = !allowed;
        if (allowed) {
          firstAllowed ??= opt;
          if (stock && unit.id === displayUnit(stock).id) preferred = opt;
        }
      }
      const current = select.selectedOptions[0];
      if (!current || current.disabled) {
        const pick = preferred ?? firstAllowed;
        if (pick) select.value = pick.value;
      }
    });
    const alertLabel = form!.querySelector<HTMLElement>("[data-alert-unit]");
    if (alertLabel && stock) {
      const d = displayUnit(stock);
      alertLabel.textContent = `(${d.dimension === "count" ? d.plural : d.symbol})`;
    }
  }

  function syncMethod(): void {
    const checked = form!.querySelector<HTMLInputElement>("input[data-option-method]:checked");
    const method = checked?.value ?? "package";
    form!.querySelectorAll<HTMLElement>("[data-method-fields]").forEach((block) => {
      block.hidden = block.dataset.methodFields !== method;
    });
  }

  stockSelect?.addEventListener("change", filterUnitSelects);
  form.querySelectorAll<HTMLInputElement>("input[data-option-method]").forEach((r) => r.addEventListener("change", syncMethod));
  filterUnitSelects();
  syncMethod();
}

init();
