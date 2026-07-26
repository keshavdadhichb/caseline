import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tailwind is intentionally not used: the Claude Design source specifies
// exact inline styles, and matching it 1:1 in plain CSS avoids a
// translation layer that could drift from the design.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
