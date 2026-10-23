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

type Reply = Response | Error | (() => Response | Promise<Response>);

/** Route fetch calls: GET /subscriptions → `list` (default: none), POST /subscribe → `subscribe`. */
function mockApi({ list = [], subscribe }: { list?: unknown[] | Reply; subscribe?: Reply }) {
  const reply = (r: Reply | undefined) => {
    if (r === undefined) return Promise.reject(new Error("unexpected request"));
    if (r instanceof Error) return Promise.reject(r);
    if (typeof r === "function") return Promise.resolve(r());
    return Promise.resolve(r.clone());
  };
  fetchMock.mockImplementation((url: string) => {
    if (url.includes("/subscriptions")) {
      return reply(Array.isArray(list) ? jsonResponse(200, list) : list);
    }
    if (url.endsWith("/subscribe")) return reply(subscribe);
    return Promise.reject(new Error(`unexpected request ${url}`));
  });
}

function listRequests() {
  return (fetchMock.mock.calls as [string, RequestInit][]).filter(([url]) =>
    url.includes("/subscriptions"),
  );
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
  mockApi({});
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
    mockApi({ subscribe: jsonResponse(200, savedRow("tokyo", "TPE-TYO", 10000)) });
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
    mockApi({ subscribe: jsonResponse(200, savedRow("seoul", "TPE-SEL", 5500)) });
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
    mockApi({ subscribe: jsonResponse(400, { message: "Unknown plan_name" }) });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "10000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));

    expect(await within(tokyo).findByRole("alert")).toHaveTextContent(/Unknown plan_name/);
    expect(within(tokyo).getByRole("button", { name: "開始追蹤" })).toBeEnabled();
  });

  it("shows an error when the network fails, and keeps the other card usable", async () => {
    mockApi({ subscribe: new TypeError("Failed to fetch") });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "10000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));

    expect(await within(tokyo).findByRole("alert")).toHaveTextContent(/訂閱失敗/);
    expect(within(card(/台北 ✈ 首爾/)).queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("Dashboard subscribed state", () => {
  it("loads the signed-in user's subscriptions and marks the subscribed plans", async () => {
    mockApi({ list: [savedRow("tokyo", "TPE-TYO", 10000)] });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    expect(await within(tokyo).findByText("已訂閱")).toBeInTheDocument();
    expect(within(tokyo).getByText(/目前目標.*NT\$10,000/)).toBeInTheDocument();
    expect(within(tokyo).getByRole("button", { name: "更新目標價" })).toBeInTheDocument();
    expect(listRequests()[0]?.[0]).toBe(`${API}/subscriptions?email=amy%40example.com`);

    const seoul = card(/台北 ✈ 首爾/);
    expect(within(seoul).queryByText("已訂閱")).not.toBeInTheDocument();
    expect(within(seoul).getByRole("button", { name: "開始追蹤" })).toBeInTheDocument();
  });

  it("flips a card to subscribed as soon as the POST succeeds, without refetching the list", async () => {
    mockApi({ subscribe: jsonResponse(200, savedRow("seoul", "TPE-SEL", 5500)) });
    renderDashboard();
    await waitFor(() => expect(listRequests()).toHaveLength(1));

    const seoul = card(/台北 ✈ 首爾/);
    fireEvent.change(within(seoul).getByLabelText(/目標價/), { target: { value: "5500" } });
    fireEvent.click(within(seoul).getByRole("button", { name: "開始追蹤" }));

    expect(await within(seoul).findByText("已訂閱")).toBeInTheDocument();
    expect(within(seoul).getByText(/目前目標.*NT\$5,500/)).toBeInTheDocument();
    expect(within(seoul).getByRole("button", { name: "更新目標價" })).toBeInTheDocument();
    expect(within(card(/台北 ✈ 東京/)).queryByText("已訂閱")).not.toBeInTheDocument();
    expect(listRequests()).toHaveLength(1);
  });

  it("updating the target overwrites the old one on the card", async () => {
    mockApi({
      list: [savedRow("tokyo", "TPE-TYO", 10000)],
      subscribe: jsonResponse(200, savedRow("tokyo", "TPE-TYO", 8000)),
    });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    await within(tokyo).findByText(/目前目標.*NT\$10,000/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "8000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "更新目標價" }));

    expect(await within(tokyo).findByText(/目前目標.*NT\$8,000/)).toBeInTheDocument();
    expect(within(tokyo).queryByText(/目前目標.*NT\$10,000/)).not.toBeInTheDocument();
    expect(sentBody()).toEqual({
      email: "amy@example.com",
      plan_name: "tokyo",
      target_price: 8000,
    });
  });

  it.each([
    ["the server errors", jsonResponse(500, { message: "boom" })],
    ["the network fails", new TypeError("Failed to fetch")],
  ])("still renders every plan as unsubscribed when %s", async (_label, failure) => {
    mockApi({ list: failure, subscribe: jsonResponse(200, savedRow("tokyo", "TPE-TYO", 9000)) });
    renderDashboard();
    await waitFor(() => expect(listRequests()).toHaveLength(1));

    for (const name of [/台北 ✈ 東京/, /台北 ✈ 首爾/]) {
      expect(within(card(name)).queryByText("已訂閱")).not.toBeInTheDocument();
      expect(within(card(name)).getByRole("button", { name: "開始追蹤" })).toBeEnabled();
    }

    // Subscribing still works and flips the card.
    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "9000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));
    expect(await within(tokyo).findByText("已訂閱")).toBeInTheDocument();
  });
});
