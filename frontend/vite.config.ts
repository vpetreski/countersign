import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { searchForWorkspaceRoot } from "vite";
import { defineConfig } from "vitest/config";

// Production serves the API and the UI from one origin; locally both servers proxy /api to the backend.
const proxy = { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy,
    // The Evals view falls back to the backend's published report in development when the API has none.
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), "../backend/countersign/evals/published"] },
  },
  preview: { port: 5173, strictPort: true, proxy },
  build: { target: "es2022", sourcemap: false, chunkSizeWarningLimit: 700 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
  },
});
