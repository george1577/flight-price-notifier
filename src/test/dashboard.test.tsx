import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router";
import type { User } from "@supabase/supabase-js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppPage from "@/pages/App";

// Supabase is the auth boundary; the dashboard only needs it for sign-out.
vi.mock("@/integrations/supabase/client", () => ({
  supabase: { auth: { signOut: vi.fn() } },
}));

const API = "https://api.example.test";
const user = { id: "u1", email: "amy@example.com" } as User;

let fetchMock: ReturnType<typeof vi.fn>;

function renderDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(
    [
      {
        element: (
          <QueryClientProvider client={queryClient}>
            <Outlet context={{ user }} />
          </QueryClientProvider>
        ),
        children: [{ path: "/app", element: <AppPage /> }],
      },
    ],
    { initialEntries: ["/app"] },
  );
  render(<RouterProvider router={router} />);
}

function card(name: RegExp) {
  return screen.getByRole("region", { name });
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function savedRow(plan_name: string, route: string, target_price: number) {
  return {
    email: user.email,
    route,
    plan_name,
    origin: "TPE",
    destination: route.slice(4),
    target_price,
    currency: "TWD",
    created_at: "2026-10-23T00:00:00Z",
    updated_at: "2026-10-23T00:00:00Z",
  };
}

function subscribeRequests() {
  return (fetchMock.mock.calls as [string, RequestInit][]).filter(([url]) =>
    url.endsWith("/subscribe"),
  );
}

function sentBody(index = 0) {
  return JSON.parse(String(subscribeRequests()[index]?.[1].body));
}

beforeEach(() => {
  vi.stubEnv("VITE_FLIGHT_API_URL", API);
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("Dashboard plan cards", () => {
  it("shows the Tokyo and Seoul plans with a current-fare hint", () => {
    renderDashboard();

    expect(within(card(/台北 ✈ 東京/)).getByText(/NT\$9,325/)).toBeInTheDocument();
    expect(within(card(/台北 ✈ 首爾/)).getByText(/NT\$5,989/)).toBeInTheDocument();
  });

  it("subscribes to a plan by POSTing the email, plan and target to the API", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, savedRow("tokyo", "TPE-TYO", 10000)));
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "10000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));

    await waitFor(() => expect(subscribeRequests()).toHaveLength(1));
    const [url, init] = subscribeRequests()[0]!;
    expect(url).toBe(`${API}/subscribe`);
    expect(init.method).toBe("POST");
    expect(sentBody()).toEqual({
      email: "amy@example.com",
      plan_name: "tokyo",
      target_price: 10000,
    });
    expect(await within(tokyo).findByText(/已開始追蹤/)).toBeInTheDocument();
  });

  it("sends the Seoul plan from the Seoul card", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, savedRow("seoul", "TPE-SEL", 5500)));
    renderDashboard();

    const seoul = card(/台北 ✈ 首爾/);
    fireEvent.change(within(seoul).getByLabelText(/目標價/), { target: { value: "5,500" } });
    fireEvent.click(within(seoul).getByRole("button", { name: "開始追蹤" }));

    await waitFor(() => expect(subscribeRequests()).toHaveLength(1));
    expect(sentBody()).toEqual({
      email: "amy@example.com",
      plan_name: "seoul",
      target_price: 5500,
    });
  });

  it.each([
    ["missing", ""],
    ["zero", "0"],
    ["negative", "-500"],
    ["non-numeric", "cheap"],
  ])("blocks a %s target before sending", async (_label, value) => {
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));

    expect(await within(tokyo).findByRole("alert")).toHaveTextContent(/大於 0/);
    expect(subscribeRequests()).toHaveLength(0);
  });

  it("shows the API's message when the save is rejected", async () => {
    fetchMock.mockResolvedValue(jsonResponse(400, { message: "Unknown plan_name" }));
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "10000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));

    expect(await within(tokyo).findByRole("alert")).toHaveTextContent(/Unknown plan_name/);
    expect(within(tokyo).getByRole("button", { name: "開始追蹤" })).toBeEnabled();
  });

  it("shows an error when the network fails, and keeps the other card usable", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "10000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));

    expect(await within(tokyo).findByRole("alert")).toHaveTextContent(/訂閱失敗/);
    expect(within(card(/台北 ✈ 首爾/)).queryByRole("alert")).not.toBeInTheDocument();
  });
});
