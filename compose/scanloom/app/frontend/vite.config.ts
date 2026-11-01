import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const ui = fileURLToPath(new URL("../../../../lib/batlab-ui/src", import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  worker: { format: "es" },
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)), "@batlab/ui": ui },
    dedupe: ["react", "react-dom", "react-router", "lucide-react", "sonner", "clsx", "tailwind-merge"],
  },
  server: { fs: { allow: [".", ui] }, proxy: { "/api": process.env.SCANLOOM_API ?? "http://localhost:8850" } },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: (id: string) => (/node_modules\/(react|react-dom|react-router|scheduler|@tanstack)\//.test(id) ? "vendor" : undefined),
      },
    },
  },
});
