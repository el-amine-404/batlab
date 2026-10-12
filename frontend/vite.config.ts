import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  base: "/",
  server: { proxy: { "/api": "http://127.0.0.1:6868", "/health": "http://127.0.0.1:6868" } },
  build: { outDir: "../backend/stanzarr/static", emptyOutDir: true },
})
