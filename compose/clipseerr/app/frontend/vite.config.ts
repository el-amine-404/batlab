import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  // `npm run dev` against a running clipseerr (CLIPSEERR_API=http://host:8820).
  server: { proxy: { "/api": process.env.CLIPSEERR_API ?? "http://localhost:8820" } },
  build: {
    outDir: "dist",
    sourcemap: false,
    // React and the router change least: their own chunk stays cached across releases.
    rollupOptions: {
      output: {
        manualChunks: (id: string) =>
          /node_modules\/(react|react-dom|react-router|scheduler|@tanstack)\//.test(id) ? "vendor" : undefined,
      },
    },
  },
});
