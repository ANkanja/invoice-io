/**
 * Prevents accidental double submission of POST forms (double-click, impatient
 * second tap on a slow phone connection). The server has its own protection
 * for money (e.g. a unique token per payment form); this is the friendly
 * first line of defence and shows that something is happening.
 */
export function initSubmitGuards(): void {
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    // getAttribute, not form.method: a field named "method" would shadow that property.
    const method = (form.getAttribute("method") ?? "get").toLowerCase();
    if (method !== "post" || form.hasAttribute("data-allow-resubmit")) return;
    if (event.defaultPrevented) return;

    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");

    const submitter = (event as SubmitEvent).submitter;
    // Disable after the browser has captured the submitter's name/value.
    window.setTimeout(() => {
      form.querySelectorAll<HTMLButtonElement>("button[type=submit], button:not([type])").forEach((btn) => {
        btn.disabled = true;
      });
      if (submitter instanceof HTMLButtonElement && submitter.dataset.busyText) {
        submitter.textContent = submitter.dataset.busyText;
      }
    }, 0);
  });

  // Coming back with the browser's Back button restores a cached page: re-enable it.
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    document.querySelectorAll<HTMLFormElement>("form[data-submitting]").forEach((form) => {
      delete form.dataset.submitting;
      form.removeAttribute("aria-busy");
      form.querySelectorAll<HTMLButtonElement>("button").forEach((btn) => (btn.disabled = false));
    });
  });
}
