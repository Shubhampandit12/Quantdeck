import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api and /ws to the FastAPI backend on :8000 so the SPA
// and API share an origin during development.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // Mirrors the "@/*" path in tsconfig.json — shadcn-style components
      // (components/ui/*, lib/utils) import via "@/...", so Vite needs its
      // own alias too (tsconfig paths only affect the type checker).
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  build: { outDir: "dist" },
});
