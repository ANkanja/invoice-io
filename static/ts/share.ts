/**
 * WhatsApp and Email buttons for a picture: an issued invoice or a poster export.
 * The page provides the picture and message as JSON in #share-data.
 *
 * - Where the browser can share files (most phones, when the app is opened
 *   over HTTPS or on the same device), the button opens the phone's share menu
 *   with the invoice image attached: pick WhatsApp, Gmail or any other app.
 * - Otherwise the image is downloaded and WhatsApp (wa.me) or the email app
 *   opens with the customer and the message filled in; a note says to attach
 *   the downloaded image.
 * The app itself never sends anything.
 */

interface ShareImageData {
  imageUrl: string;
  filename: string;
  subject: string;
  text: string;
  whatsapp: string; // digits with country code, or "" to pick the chat in WhatsApp
  email: string;
}

function readData(): ShareImageData | null {
  const el = document.getElementById("share-data");
  if (!el?.textContent) return null;
  try {
    return JSON.parse(el.textContent) as ShareImageData;
  } catch {
    return null;
  }
}

function canShareFiles(filename: string): boolean {
  if (typeof navigator.canShare !== "function") return false;
  try {
    return navigator.canShare({ files: [new File([""], filename, { type: "image/png" })] });
  } catch {
    return false;
  }
}

function init(): void {
  const data = readData();
  const buttons = document.querySelectorAll<HTMLButtonElement>("[data-share]");
  const note = document.querySelector<HTMLElement>("[data-share-note]");
  if (!data || !buttons.length) return;

  // Browsers only open the share menu straight from a tap, so fetch the image now.
  let file: File | null = null;
  if (canShareFiles(data.filename)) {
    fetch(data.imageUrl, { credentials: "same-origin" })
      .then((response) => (response.ok ? response.blob() : Promise.reject(new Error(String(response.status)))))
      .then((blob) => {
        file = new File([blob], data.filename, { type: "image/png" });
      })
      .catch(() => {
        file = null;
      });
  }

  const say = (text: string): void => {
    if (!note) return;
    note.textContent = text;
    note.hidden = false;
  };

  function downloadImage(): void {
    const link = document.createElement("a");
    link.href = `${data!.imageUrl}?download=1`;
    link.download = data!.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  function openApp(target: string): void {
    downloadImage();
    if (target === "whatsapp") {
      window.open(`https://wa.me/${data!.whatsapp}?text=${encodeURIComponent(data!.text)}`, "_blank", "noopener");
      say(
        data!.whatsapp
          ? `The image has been downloaded (${data!.filename}). WhatsApp is opening with the message ready: attach the image, then send.`
          : `The image has been downloaded (${data!.filename}). Choose the chat in WhatsApp, attach the image and send.`,
      );
    } else {
      window.location.href =
        `mailto:${data!.email}?subject=${encodeURIComponent(data!.subject)}&body=${encodeURIComponent(data!.text)}`;
      say(
        `The image has been downloaded (${data!.filename}). Your email app is opening with the message ready: attach the image, then send.` +
          (data!.email ? "" : " Type the email address in."),
      );
    }
  }

  buttons.forEach((button) =>
    button.addEventListener("click", () => {
      const target = button.dataset.share ?? "";
      if (!file) {
        openApp(target);
        return;
      }
      navigator.share({ files: [file], title: data.subject, text: data.text }).catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return; // closed the share menu
        openApp(target);
      });
    }),
  );
}

init();

export {};
