import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Why this file exists: standard Vite entrypoint config. Dev server proxies
// /api requests to the FastAPI backend (default http://localhost:8000) so
// the frontend can call relative paths ("/api/chat") without CORS friction
// during local development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/health": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
