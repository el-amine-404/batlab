import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  // `npm run dev` against a running songdrop (SONGDROP_API=http://host:8810).
  server: { proxy: { "/api": process.env.SONGDROP_API ?? "http://localhost:8810" } },
  // One bundle (~560 kB, 175 kB gzipped) is fine for a page on the home network.
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 800 },
});
