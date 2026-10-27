/**
 * Business settings page: instant logo preview and a friendly size check
 * before upload. The server re-validates everything (type, size, real image).
 */

function initLogoPreview(): void {
  const input = document.querySelector<HTMLInputElement>("input[type=file][name=logo]");
  const preview = document.getElementById("logo-preview");
  if (!input || !(preview instanceof HTMLImageElement)) return;

  const maxBytes = Number(input.dataset.maxMb ?? "5") * 1024 * 1024;
  const feedback = document.getElementById("logo-client-error");
  let objectUrl: string | null = null;

  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (feedback) feedback.textContent = "";
    input.classList.remove("is-invalid");
    if (!file) return;

    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      showError("Choose a PNG, JPEG or WebP image.");
      return;
    }
    if (file.size > maxBytes) {
      showError(`That image is ${(file.size / 1024 / 1024).toFixed(1)} MB. The limit is ${input.dataset.maxMb} MB.`);
      return;
    }
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    preview.src = objectUrl;
    preview.hidden = false;
  });

  function showError(message: string): void {
    input!.value = "";
    input!.classList.add("is-invalid");
    if (feedback) feedback.textContent = message;
  }
}

initLogoPreview();
