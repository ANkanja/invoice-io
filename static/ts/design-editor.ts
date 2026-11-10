/**
 * Design editor: draw the boxes on a poster design.
 *
 * - The design picture is shown at the width of the page; boxes are positioned
 *   in percentages, so they stay in place on any screen. All numbers kept and
 *   sent are pixels on the original picture.
 * - Drag a box to move it, drag its corner to resize it, or type exact
 *   numbers. Arrow keys nudge the selected box (Shift: 10 px).
 * - On save the boxes are sent as JSON; the server checks every value
 *   (posters.services.parse_fields) before anything is stored.
 */

interface BoxData {
  key: string;
  source: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  font: number | null;
  size: number;
  min_size: number;
  color: string;
  align: string;
  valign: string;
  max_lines: number;
  line_spacing: number;
  uppercase: boolean;
  fit: string;
}

interface SourceInfo {
  value: string;
  label: string;
  image: boolean;
}

interface DesignEditorData {
  width: number;
  height: number;
  background: string;
  boxes: BoxData[];
  fonts: { id: number; name: string }[];
  sources: SourceInfo[];
  aligns: [string, string][];
  valigns: [string, string][];
  fits: [string, string][];
}

const MIN_BOX = 10;

function readData(): DesignEditorData | null {
  const el = document.getElementById("design-editor-data");
  if (!el?.textContent) return null;
  try {
    return JSON.parse(el.textContent) as DesignEditorData;
  } catch {
    return null;
  }
}

const clamp = (value: number, low: number, high: number): number => Math.min(Math.max(value, low), high);

function element<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> = {},
  className = ""): HTMLElementTagNameMap[K] {
  const el = Object.assign(document.createElement(tag), props);
  if (className) el.className = className;
  return el;
}

function init(): void {
  const data = readData();
  const form = document.querySelector<HTMLFormElement>("form[data-design-form]");
  const canvas = document.querySelector<HTMLElement>("[data-design-canvas]");
  const hidden = document.querySelector<HTMLInputElement>("[data-fields-json]");
  const panel = document.querySelector<HTMLElement>("[data-box-fields]");
  const noSelection = document.querySelector<HTMLElement>("[data-no-selection]");
  const addSource = document.querySelector<HTMLSelectElement>("[data-add-source]");
  const addButton = document.querySelector<HTMLButtonElement>("[data-add-box]");
  if (!data || !form || !canvas || !hidden || !panel || !addSource || !addButton) return;

  const W = data.width;
  const H = data.height;
  const boxes: BoxData[] = data.boxes.map((b) => ({ ...b }));
  const isImage = (source: string): boolean => data.sources.find((s) => s.value === source)?.image ?? false;
  let selected = -1;
  let dirty = false;

  // ---- the picture and its boxes ------------------------------------------------------
  const picture = element("img", { src: data.background, alt: "Design", draggable: false }, "design-canvas-image");
  const layer = element("div", {}, "design-canvas-layer");
  canvas.replaceChildren(picture, layer);
  const scale = (): number => (canvas.clientWidth || W) / W;

  function place(el: HTMLElement, box: BoxData): void {
    el.style.left = `${(box.x / W) * 100}%`;
    el.style.top = `${(box.y / H) * 100}%`;
    el.style.width = `${(box.w / W) * 100}%`;
    el.style.height = `${(box.h / H) * 100}%`;
  }

  function drawBoxes(): void {
    layer.replaceChildren(
      ...boxes.map((box, index) => {
        const el = element("div", { tabIndex: 0, title: box.label }, `design-box${index === selected ? " is-selected" : ""}`);
        el.dataset.index = String(index);
        el.setAttribute("role", "button");
        el.setAttribute("aria-label", `${box.label} box`);
        el.append(element("span", { textContent: box.label }, "design-box-label"),
          element("span", { title: "Drag to resize" }, "design-box-handle"));
        place(el, box);
        return el;
      }),
    );
  }

  function select(index: number): void {
    selected = index;
    layer.querySelectorAll<HTMLElement>(".design-box").forEach((el) =>
      el.classList.toggle("is-selected", Number(el.dataset.index) === index));
    buildPanel();
  }

  function changed(): void {
    dirty = true;
  }

  // ---- dragging -------------------------------------------------------------------------
  let drag: { index: number; mode: "move" | "resize"; startX: number; startY: number; orig: BoxData } | null = null;

  layer.addEventListener("pointerdown", (event) => {
    const target = event.target as HTMLElement;
    const el = target.closest<HTMLElement>(".design-box");
    if (!el) return;
    const index = Number(el.dataset.index);
    const box = boxes[index];
    if (!box) return;
    event.preventDefault();
    if (selected !== index) select(index);
    el.focus({ preventScroll: true });
    el.setPointerCapture(event.pointerId);
    drag = { index, mode: target.classList.contains("design-box-handle") ? "resize" : "move",
      startX: event.clientX, startY: event.clientY, orig: { ...box } };
  });

  layer.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const box = boxes[drag.index];
    if (!box) return;
    const dx = Math.round((event.clientX - drag.startX) / scale());
    const dy = Math.round((event.clientY - drag.startY) / scale());
    if (drag.mode === "move") {
      box.x = clamp(drag.orig.x + dx, 0, W - box.w);
      box.y = clamp(drag.orig.y + dy, 0, H - box.h);
    } else {
      box.w = clamp(drag.orig.w + dx, MIN_BOX, W - box.x);
      box.h = clamp(drag.orig.h + dy, MIN_BOX, H - box.y);
    }
    const el = layer.querySelector<HTMLElement>(`.design-box[data-index="${drag.index}"]`);
    if (el) place(el, box);
    syncNumbers();
    changed();
  });

  const endDrag = (): void => {
    drag = null;
  };
  layer.addEventListener("pointerup", endDrag);
  layer.addEventListener("pointercancel", endDrag);

  layer.addEventListener("keydown", (event) => {
    const el = (event.target as HTMLElement).closest<HTMLElement>(".design-box");
    const box = el ? boxes[Number(el.dataset.index)] : undefined;
    if (!el || !box) return;
    const step = event.shiftKey ? 10 : 1;
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step],
    };
    const move = moves[event.key];
    if (move) {
      event.preventDefault();
      box.x = clamp(box.x + move[0], 0, W - box.w);
      box.y = clamp(box.y + move[1], 0, H - box.h);
      place(el, box);
      syncNumbers();
      changed();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select(Number(el.dataset.index));
    }
  });

  // ---- the selected box's settings ----------------------------------------------------
  const numberInputs = new Map<keyof BoxData, HTMLInputElement>();

  function syncNumbers(): void {
    const box = boxes[selected];
    if (!box) return;
    for (const [name, input] of numberInputs) {
      if (document.activeElement !== input) input.value = String(box[name]);
    }
  }

  function field(labelText: string, control: HTMLElement, wide = false): HTMLElement {
    const id = `box-${labelText.toLowerCase().replace(/[^a-z]+/g, "-")}`;
    control.id = id;
    const wrap = element("div", {}, wide ? "col-12 mb-2" : "col-6 mb-2");
    wrap.append(element("label", { htmlFor: id, textContent: labelText }, "form-label small mb-1"), control);
    return wrap;
  }

  function selectControl(options: [string, string][], value: string): HTMLSelectElement {
    const el = element("select", {}, "form-select form-select-sm");
    el.replaceChildren(...options.map(([v, text]) => new Option(text, v, false, v === value)));
    return el;
  }

  function buildPanel(): void {
    const box = boxes[selected];
    numberInputs.clear();
    panel!.hidden = !box;
    if (noSelection) noSelection.hidden = Boolean(box);
    if (!box) {
      panel!.replaceChildren();
      return;
    }
    const row = element("div", {}, "row g-2");
    const update = (apply: () => void, redraw = false): void => {
      apply();
      changed();
      if (redraw) {
        drawBoxes();
        buildPanel();
      } else {
        const el = layer.querySelector<HTMLElement>(`.design-box[data-index="${selected}"]`);
        if (el) {
          place(el, box);
          el.title = box.label;
          const label = el.querySelector(".design-box-label");
          if (label) label.textContent = box.label;
        }
      }
    };

    const label = element("input", { value: box.label, maxLength: 60 }, "form-control form-control-sm");
    label.addEventListener("input", () => update(() => (box.label = label.value || box.label)));
    row.append(field("Label", label));

    const source = selectControl(data!.sources.map((s) => [s.value, s.label]), box.source);
    source.addEventListener("change", () => update(() => {
      box.source = source.value;
      box.label = data!.sources.find((s) => s.value === source.value)?.label ?? box.label;
    }, true));
    row.append(field("What goes in it", source));

    for (const name of ["x", "y", "w", "h"] as const) {
      const input = element("input", { type: "number", min: "0", step: "1", value: String(box[name]) },
        "form-control form-control-sm");
      input.addEventListener("input", () => update(() => {
        const value = Math.round(Number(input.value));
        if (!Number.isFinite(value)) return;
        if (name === "x") box.x = clamp(value, 0, W - box.w);
        if (name === "y") box.y = clamp(value, 0, H - box.h);
        if (name === "w") box.w = clamp(value, MIN_BOX, W - box.x);
        if (name === "h") box.h = clamp(value, MIN_BOX, H - box.y);
      }));
      input.addEventListener("change", () => (input.value = String(box[name])));
      numberInputs.set(name, input);
      const labels = { x: "From left (px)", y: "From top (px)", w: "Width (px)", h: "Height (px)" };
      row.append(field(labels[name], input));
    }

    if (isImage(box.source)) {
      const fit = selectControl(data!.fits, box.fit);
      fit.addEventListener("change", () => update(() => (box.fit = fit.value)));
      row.append(field("Picture", fit, true));
    } else {
      const font = selectControl(data!.fonts.map((f) => [String(f.id), f.name]), String(box.font ?? ""));
      if (box.font === null && data!.fonts[0]) box.font = data!.fonts[0].id;
      font.addEventListener("change", () => update(() => (box.font = Number(font.value))));
      row.append(field("Font", font, true));

      const numeric = (name: "size" | "min_size" | "max_lines", text: string, low: number, high: number): void => {
        const input = element("input", { type: "number", min: String(low), max: String(high), step: "1",
          value: String(box[name]) }, "form-control form-control-sm");
        input.addEventListener("input", () => update(() => {
          const value = Math.round(Number(input.value));
          if (Number.isFinite(value)) box[name] = clamp(value, low, high);
          if (box.min_size > box.size) box.min_size = box.size;
        }));
        row.append(field(text, input));
      };
      numeric("size", "Text size", 6, 400);
      numeric("min_size", "Smallest size", 6, 400);
      numeric("max_lines", "Most lines", 1, 40);

      const spacing = element("input", { type: "number", min: "0.8", max: "2.5", step: "0.05",
        value: String(box.line_spacing) }, "form-control form-control-sm");
      spacing.addEventListener("input", () => update(() => {
        const value = Number(spacing.value);
        if (Number.isFinite(value)) box.line_spacing = clamp(value, 0.8, 2.5);
      }));
      row.append(field("Line spacing", spacing));

      const color = element("input", { type: "color", value: box.color }, "form-control form-control-sm form-control-color");
      color.addEventListener("input", () => update(() => (box.color = color.value.toUpperCase())));
      row.append(field("Colour", color));

      const align = selectControl(data!.aligns, box.align);
      align.addEventListener("change", () => update(() => (box.align = align.value)));
      row.append(field("Line up across", align));
      const valign = selectControl(data!.valigns, box.valign);
      valign.addEventListener("change", () => update(() => (box.valign = valign.value)));
      row.append(field("Line up down", valign));

      const upper = element("input", { type: "checkbox", checked: box.uppercase }, "form-check-input");
      upper.id = "box-uppercase";
      upper.addEventListener("change", () => update(() => (box.uppercase = upper.checked)));
      const check = element("div", {}, "col-12 form-check ms-2 mb-2");
      check.append(upper, element("label", { htmlFor: "box-uppercase", textContent: "CAPITAL LETTERS" }, "form-check-label"));
      row.append(check);
    }

    const remove = element("button", { type: "button", textContent: "Remove this box" }, "btn btn-outline-danger btn-sm mt-1");
    remove.addEventListener("click", () => {
      boxes.splice(selected, 1);
      selected = -1;
      changed();
      drawBoxes();
      buildPanel();
    });
    panel!.replaceChildren(row, remove);
  }

  // ---- adding boxes ---------------------------------------------------------------------
  addSource.replaceChildren(...data.sources.map((s) => new Option(s.label, s.value)));

  function uniqueKey(base: string): string {
    if (!boxes.some((b) => b.key === base)) return base;
    let n = 2;
    while (boxes.some((b) => b.key === `${base}_${n}`)) n += 1;
    return `${base}_${n}`;
  }

  addButton.addEventListener("click", () => {
    const source = data.sources.find((s) => s.value === addSource.value);
    if (!source) return;
    const w = Math.round(W * (source.image ? 0.2 : 0.7));
    const h = Math.round(H * (source.image ? 0.1 : source.value === "message" ? 0.2 : 0.08));
    const size = Math.max(12, Math.round((source.value === "message" ? h / 6 : h * 0.55)));
    const preferred = data.fonts.find((f) => /semibold|bold/i.test(f.name)) ?? data.fonts[0];
    boxes.push({
      key: uniqueKey(source.value), source: source.value, label: source.label,
      x: Math.round((W - w) / 2), y: Math.round((H - h) / 2), w, h,
      font: preferred?.id ?? null, size, min_size: Math.max(10, Math.round(size / 2)), color: "#1C2B28",
      align: "left", valign: "top", max_lines: source.value === "message" ? 6 : 2, line_spacing: 1.2,
      uppercase: false, fit: "contain",
    });
    changed();
    drawBoxes();
    select(boxes.length - 1);
  });

  // ---- saving ---------------------------------------------------------------------------
  form.addEventListener("input", (event) => {
    if ((event.target as HTMLElement).id === "design-name") changed();
  });
  form.addEventListener("submit", () => {
    hidden.value = JSON.stringify(boxes);
    dirty = false;
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty) event.preventDefault();
  });

  drawBoxes();
  buildPanel();
}

init();

export {};
