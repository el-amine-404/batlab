import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The frame shared by batlab's apps (lib/batlab-ui): source, built with the
// page; what it imports comes from this app's node_modules (dedupe).
const ui = fileURLToPath(new URL("../../../../lib/batlab-ui/src", import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)), "@batlab/ui": ui },
    dedupe: ["react", "react-dom", "react-router", "lucide-react", "sonner", "clsx", "tailwind-merge"],
  },
  // `npm run dev` against a running stanzarr (STANZARR_API=http://host:6868).
  server: { fs: { allow: [".", ui] }, proxy: { "/api": process.env.STANZARR_API ?? "http://localhost:6868" } },
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
