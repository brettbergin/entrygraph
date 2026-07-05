import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build to ../src/entrygraph/sentinel/static so the FastAPI app can serve the
// dashboard at /ui from the same deployment (see app.py). `base: "/ui/"` keeps
// asset URLs correct under that mount. During local dev, `npm run dev` proxies
// API calls to a Sentinel instance on :8000.
export default defineConfig({
  plugins: [react()],
  base: "/ui/",
  build: {
    outDir: "../src/entrygraph/sentinel/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
