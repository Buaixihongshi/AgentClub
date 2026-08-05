import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backendOrigin = env.YIREN_BACKEND_ORIGIN ?? "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": backendOrigin,
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/tests/setup.ts"],
    }
  };
});
