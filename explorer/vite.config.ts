import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build into the package's static dir so `entrygraph explore serve` serves the UI
// at /. In dev, `npm run dev` proxies /api to an explorer server on :8100.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../src/entrygraph/explore/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8100",
    },
  },
});
