import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// Dev: `npm run dev` proxies /api to the gateway (default http://127.0.0.1:8080). Build output goes to dist/ and is served by the gateway.
export default defineConfig({ plugins: [react()], server: { proxy: { "/api": process.env.VITE_API_TARGET ?? "http://127.0.0.1:8080" } } });
