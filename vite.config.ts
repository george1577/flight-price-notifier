import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { execFileSync } from "node:child_process";
import path from "node:path";

/**
 * Dev server only: GET /__backend_version answers the id of the backend code in this
 * checkout (infra/backend-version.sh), recomputed on every request. BackendVersionBanner
 * compares it with the deployed stack's GET /version.
 */
function backendVersion(): Plugin {
  return {
    name: "backend-version",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use("/__backend_version", (_req, res) => {
        try {
          const version = execFileSync("infra/backend-version.sh", { cwd: __dirname, encoding: "utf8" });
          res.setHeader("content-type", "text/plain");
          res.setHeader("cache-control", "no-store");
          res.end(version);
        } catch {
          res.statusCode = 500;
          res.end();
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), backendVersion()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: { port: 8080 },
});
