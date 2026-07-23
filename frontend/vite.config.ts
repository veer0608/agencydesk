import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    // Proxying keeps the browser on one origin, so nothing here depends on CORS
    // being configured correctly to work.
    //
    // The default is the localhost case, because that is the one nobody
    // configures: running `npm run dev` straight from a clone has to work. Under
    // Docker the API is a sibling container rather than localhost, so
    // docker-compose.yml sets VITE_API_TARGET=http://api:8000 explicitly.
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
