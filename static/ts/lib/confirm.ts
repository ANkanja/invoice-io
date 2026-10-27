/**
 * Confirmation dialogs for destructive or irreversible actions.
 *
 * Usage in a template:
 *   <form method="post" data-confirm="Cancel invoice INV-2026-0004?"
 *         data-confirm-button="Cancel invoice"> … </form>
 *
 * Uses the single Bootstrap modal rendered in base.html (#confirm-modal), not
 * window.confirm(), so it matches the app and works with the keyboard.
 */
import { Modal } from "bootstrap";

const CONFIRMED = "confirmed";

export function initConfirmDialogs(): void {
  const modalEl = document.getElementById("confirm-modal");
  if (!modalEl) return;
  const messageEl = modalEl.querySelector<HTMLElement>("[data-confirm-message]");
  const okButton = modalEl.querySelector<HTMLButtonElement>("[data-confirm-ok]");
  if (!messageEl || !okButton) return;

  const modal = Modal.getOrCreateInstance(modalEl);
  let pending: { form: HTMLFormElement; submitter: HTMLElement | null } | null = null;

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.dataset.confirm) return;
    if (form.dataset.confirmState === CONFIRMED) {
      delete form.dataset.confirmState;
      return; // let it through
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    pending = { form, submitter: (event as SubmitEvent).submitter };
    messageEl.textContent = form.dataset.confirm;
    okButton.textContent = form.dataset.confirmButton ?? "Confirm";
    modal.show();
  }, true);

  okButton.addEventListener("click", () => {
    if (!pending) return;
    const { form, submitter } = pending;
    pending = null;
    form.dataset.confirmState = CONFIRMED;
    modal.hide();
    form.requestSubmit(submitter instanceof HTMLButtonElement || submitter instanceof HTMLInputElement ? submitter : undefined);
  });

  modalEl.addEventListener("hidden.bs.modal", () => {
    pending = null;
  });
}
