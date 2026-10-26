import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The frame shared by batlab's apps (lib/batlab-ui): source, built with the
// page; what it imports comes from this app's node_modules (dedupe).
const ui = fileURLToPath(new URL("../../../../lib/batlab-ui/src", import.meta.url));

// The build must satisfy a strict CSP (script-src 'self', no inline code).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@batlab/ui": ui },
    dedupe: ["react", "react-dom", "react-router", "lucide-react", "sonner", "clsx", "tailwind-merge"],
  },
  server: { fs: { allow: [".", ui] } },
  build: { sourcemap: false, modulePreload: { polyfill: false }, assetsInlineLimit: 0 },
});
