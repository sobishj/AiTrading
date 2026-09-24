import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind IPv4 explicitly. Without this, Node/Vite can end up listening
    // only on the IPv6 loopback (::1) on some Windows setups, which refuses
    // connections from anything resolving "localhost" to 127.0.0.1 first.
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/api/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
});
