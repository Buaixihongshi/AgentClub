import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));

function captureIdentity() {
  const python = path.join(root, ".venv/bin/python");
  try {
    return JSON.parse(execFileSync(existsSync(python) ? python : "python3",
      [path.join(root, "scripts/environment_status.py"), "identity"],
      { encoding: "utf8", timeout: 15000, stdio: ["ignore", "pipe", "ignore"] }));
  } catch {
    return { environment: "local", git_sha: null, dirty: null,
      source_digest: null, checkout_id: null, captured_at: null };
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backendOrigin = env.YIREN_BACKEND_ORIGIN ?? "http://127.0.0.1:8000";
  // Frozen at dev-server start / production build; never relabel an old
  // bundle with the Git HEAD at the time it is being served.
  const identity = captureIdentity();
  const identityMiddleware = (server: import("vite").ViteDevServer) => {
    server.middlewares.use("/__environment", (_req: unknown, res: import("node:http").ServerResponse) => {
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "no-store");
      res.end(JSON.stringify(identity));
    });
  };
  return {
    plugins: [react(), {
      name: "environment-identity",
      configureServer: identityMiddleware,
      generateBundle() {
        this.emitFile({ type: "asset", fileName: "__environment", source: JSON.stringify(identity) });
      },
    }],
    define: { __FRONTEND_IDENTITY__: JSON.stringify(identity) },
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
