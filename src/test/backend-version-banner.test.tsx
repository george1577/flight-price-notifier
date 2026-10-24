import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BackendVersionBanner } from "@/components/BackendVersionBanner";

const API = "https://api.example.test";

let fetchMock: ReturnType<typeof vi.fn>;

/** /__backend_version (the Vite dev server: your checkout) and GET /version (the stack). */
function mockVersions({ local, deployed }: { local: string | Error; deployed: string | Response | Error }) {
  fetchMock.mockImplementation((url: string) => {
    if (url === "/__backend_version") {
      return local instanceof Error ? Promise.reject(local) : Promise.resolve(new Response(`${local}\n`));
    }
    if (url === `${API}/version`) {
      if (deployed instanceof Error) return Promise.reject(deployed);
      if (deployed instanceof Response) return Promise.resolve(deployed);
      return Promise.resolve(
        new Response(JSON.stringify({ backend_version: deployed, env: "dev" }), {
          headers: { "content-type": "application/json" },
        }),
      );
    }
    return Promise.reject(new Error(`unexpected request ${url}`));
  });
}

beforeEach(() => {
  vi.stubEnv("VITE_FLIGHT_API_URL", API);
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("BackendVersionBanner (dev only)", () => {
  it("stays hidden when the deployed backend matches the checkout", async () => {
    mockVersions({ local: "b675253015df", deployed: "b675253015df" });
    render(<BackendVersionBanner />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("warns when the deployed backend is a different version", async () => {
    mockVersions({ local: "b675253015df", deployed: "0a1b2c3d4e5f" });
    render(<BackendVersionBanner />);

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("0a1b2c3d4e5f");
    expect(banner).toHaveTextContent("b675253015df");
    expect(banner).toHaveTextContent(/infra\/deploy\.sh dev/);
  });

  it("warns when the backend has no /version at all (deployed before it existed)", async () => {
    mockVersions({ local: "b675253015df", deployed: new Response("Not Found", { status: 404 }) });
    render(<BackendVersionBanner />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/沒有 \/version/);
  });

  it("stays hidden outside the Vite dev server (no local version to compare)", async () => {
    mockVersions({ local: new TypeError("Failed to fetch"), deployed: "0a1b2c3d4e5f" });
    render(<BackendVersionBanner />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("clears itself once the deploy lands", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let deployed = "0a1b2c3d4e5f";
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(
        url === "/__backend_version"
          ? new Response("b675253015df")
          : new Response(JSON.stringify({ backend_version: deployed, env: "dev" })),
      ),
    );
    render(<BackendVersionBanner />);
    await screen.findByRole("alert");

    deployed = "b675253015df";
    await vi.advanceTimersByTimeAsync(20_000);

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
});
