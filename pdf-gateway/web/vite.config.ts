import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The build must satisfy a strict CSP (script-src 'self', no inline code).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { sourcemap: false, modulePreload: { polyfill: false }, assetsInlineLimit: 0 },
});
