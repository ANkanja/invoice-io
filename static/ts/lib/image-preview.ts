/**
 * Instant preview + friendly size/type check for image uploads.
 *
 *   <input type="file" data-image-preview="photo-preview" data-max-mb="5">
 *   <img id="photo-preview" hidden>
 *   <div class="invalid-feedback" id="photo-preview-error"></div>
 *
 * The server re-validates everything; this only saves a wasted upload.
 */
const ALLOWED = ["image/png", "image/jpeg", "image/webp"];

export function initImagePreviews(root: ParentNode = document): void {
  root.querySelectorAll<HTMLInputElement>("input[type=file][data-image-preview]").forEach((input) => {
    const previewId = input.dataset.imagePreview ?? "";
    const preview = document.getElementById(previewId);
    if (!(preview instanceof HTMLImageElement)) return;
    const feedback = document.getElementById(`${previewId}-error`);
    const maxMb = Number(input.dataset.maxMb ?? "5");
    const original = { src: preview.getAttribute("src"), hidden: preview.hidden };
    let objectUrl: string | null = null;

    const reset = (message = ""): void => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = null;
      if (original.src) preview.src = original.src;
      preview.hidden = original.hidden;
      input.classList.toggle("is-invalid", message !== "");
      if (feedback) {
        feedback.textContent = message;
        feedback.classList.toggle("d-block", message !== "");
      }
    };

    input.addEventListener("change", () => {
      const file = input.files?.[0];
      if (!file) return reset();
      if (!ALLOWED.includes(file.type)) {
        input.value = "";
        return reset("Choose a PNG, JPEG or WebP image.");
      }
      if (file.size > maxMb * 1024 * 1024) {
        input.value = "";
        return reset(`That image is ${(file.size / 1024 / 1024).toFixed(1)} MB. The limit is ${maxMb} MB.`);
      }
      reset();
      objectUrl = URL.createObjectURL(file);
      preview.src = objectUrl;
      preview.hidden = false;
    });
  });
}
