/**
 * Entry point loaded on every page (see templates/base.html).
 * Page-specific behaviour lives in its own entry (e.g. settings.ts).
 */
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-sans/latin-700.css";
import "bootstrap/dist/css/bootstrap.min.css";
import "bootstrap-icons/font/bootstrap-icons.css";
import "../css/app.css";

// Registers Bootstrap's data-attribute behaviour (offcanvas sidebar, dropdowns,
// collapsible panels, dismissible alerts).
import "bootstrap";

import { initConfirmDialogs } from "./lib/confirm";
import { initSubmitGuards } from "./lib/forms";

function ready(fn: () => void): void {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fn, { once: true });
  } else {
    fn();
  }
}

ready(() => {
  initConfirmDialogs();
  initSubmitGuards();
});
