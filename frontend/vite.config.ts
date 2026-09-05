import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server proxies /api to the backend, so local development is
// same-origin exactly as production is (where FastAPI serves this bundle).
// Consequence: no CORS configuration anywhere, and no CORS surprises at deploy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
