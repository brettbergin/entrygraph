import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build into the server package's static dir so `entrygraph serve` serves the
// SPA at / (with history fallback for React Router deep links). In dev,
// `npm run dev` proxies /api and /auth to a server on :8100.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../src/entrygraph/server/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8100",
      "/auth": "http://localhost:8100",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
