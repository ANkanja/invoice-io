/**
 * Vite compiles TypeScript + CSS from static/ts into static/dist with FIXED
 * file names (main.js, main.css, …). Django templates include them with
 * {% static 'dist/main.js' %}; in production WhiteNoise adds cache-busting
 * hashes during collectstatic, so no Django-Vite bridge package is needed.
 *
 * Add a new page script by adding an entry to `input` below.
 * (This file is checked by Vite itself, not by `npm run typecheck`.)
 */
import { resolve } from "node:path";
import { defineConfig } from "vite";

export default defineConfig(({ mode }) => ({
  // Relative URLs inside CSS (fonts, icons) so WhiteNoise's manifest can rewrite them.
  base: "./",
  publicDir: false,
  build: {
    outDir: resolve(import.meta.dirname, "static/dist"),
    emptyOutDir: true,
    sourcemap: mode === "development",
    manifest: false,
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, "static/ts/main.ts"),
        settings: resolve(import.meta.dirname, "static/ts/settings.ts"),
      },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name].js",
        // CSS next to the JS (dist/main.css); fonts/icons under dist/assets/.
        assetFileNames: (info) =>
          info.names.some((n) => n.endsWith(".css")) ? "[name][extname]" : "assets/[name][extname]",
      },
    },
  },
}));
