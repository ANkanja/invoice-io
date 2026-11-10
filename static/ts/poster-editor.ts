/**
 * Poster page: live preview.
 *
 * Whenever the text or the design changes, the form is sent (debounced) to the
 * preview endpoint, which draws the poster with the same renderer as the
 * export and returns a smaller JPEG. Boxes whose text had to be cut short are
 * listed under the preview. Nothing is saved until Save or Export is pressed.
 */

function init(): void {
  const form = document.querySelector<HTMLFormElement>("form[data-poster-form]");
  const image = document.querySelector<HTMLImageElement>("[data-poster-preview]");
  const wait = document.querySelector<HTMLElement>("[data-poster-wait]");
  const warnings = document.querySelector<HTMLElement>("[data-poster-warnings]");
  const url = form?.dataset.previewUrl;
  if (!form || !image || !url) return;

  let timer = 0;
  let controller: AbortController | null = null;
  let shownUrl = "";
  let dirty = false;

  async function refresh(): Promise<void> {
    controller?.abort();
    controller = new AbortController();
    const body = new FormData(form!);
    try {
      const response = await fetch(url!, { method: "POST", body, credentials: "same-origin", signal: controller.signal });
      if (!response.ok) throw new Error(String(response.status));
      const next = URL.createObjectURL(await response.blob());
      image!.src = next;
      image!.hidden = false;
      if (shownUrl) URL.revokeObjectURL(shownUrl);
      shownUrl = next;
      if (wait) wait.hidden = true;
      let list: string[] = [];
      try {
        list = JSON.parse(response.headers.get("X-Poster-Warnings") ?? "[]") as string[];
      } catch {
        list = [];
      }
      if (warnings) {
        warnings.textContent = list.join(" ");
        warnings.hidden = list.length === 0;
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (wait) {
        wait.textContent = "The preview isn't available right now. Saving still works.";
        wait.hidden = false;
      }
    }
  }

  const schedule = (): void => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => void refresh(), 350);
  };
  form.addEventListener("input", () => {
    dirty = true;
    schedule();
  });
  form.addEventListener("change", () => {
    dirty = true;
    schedule();
  });
  form.addEventListener("submit", () => (dirty = false));
  window.addEventListener("beforeunload", (event) => {
    if (dirty) event.preventDefault();
  });
  void refresh();
}

init();

export {};
