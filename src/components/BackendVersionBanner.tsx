import { useEffect, useState } from "react";

/** How often to re-check, so the banner clears by itself once a deploy lands. */
const RECHECK_MS = 15_000;

type Mismatch = { deployed: string | null; local: string };

/**
 * Dev only (mounted from main.tsx under `import.meta.env.DEV`): warns when the backend the
 * app talks to isn't the backend code in your checkout, e.g. a push whose deploy is still
 * running or failed. Without it, a stale backend looks like a button that does nothing.
 *
 * Local: `/__backend_version`, served by the Vite dev server from infra/backend-version.sh.
 * Deployed: GET <api>/version, stamped by infra/deploy.sh. Outside `vite dev` there is no
 * local version, and the banner stays hidden.
 */
export function BackendVersionBanner() {
  const [mismatch, setMismatch] = useState<Mismatch | null>(null);

  useEffect(() => {
    let stopped = false;
    async function check() {
      const local = await fetchText("/__backend_version");
      if (local === null) return;
      const api = String(import.meta.env["VITE_FLIGHT_API_URL"] ?? "").replace(/\/+$/, "");
      const deployed = await fetchDeployedVersion(`${api}/version`);
      if (!stopped) setMismatch(deployed === local ? null : { deployed, local });
    }
    void check();
    const timer = setInterval(() => void check(), RECHECK_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  if (mismatch === null) return null;
  return (
    <div
      role="alert"
      className="sticky top-0 z-50 border-b-2 border-foreground bg-marigold px-4 py-2 text-sm font-medium text-foreground"
    >
      {mismatch.deployed === null
        ? "開發用後端沒有 /version：dev stack 可能還沒部署最新版本。"
        : `開發用後端版本 ${mismatch.deployed} 與本機程式碼 ${mismatch.local} 不一致。`}{" "}
      後端相關的功能可能無法正常運作。請執行 <code>infra/deploy.sh dev</code>，或等 GitHub Actions
      部署完成（<code>gh run watch</code>）。
    </div>
  );
}

async function fetchText(url: string) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    return response.ok ? (await response.text()).trim() : null;
  } catch {
    return null;
  }
}

/** The deployed version, or null if the backend can't say (no /version: an older deploy). */
async function fetchDeployedVersion(url: string) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) return null;
    const body = (await response.json()) as { backend_version?: unknown };
    return typeof body.backend_version === "string" ? body.backend_version : null;
  } catch {
    return null;
  }
}
