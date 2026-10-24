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

function renderDashboard(entry = "/app") {
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
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
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

const CHECKOUT_HTML =
  '<!doctype html><html><body><form method="post" action="https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5">' +
  '<input type="hidden" name="MerchantTradeNo" value="FP2610241200001A2B3C"></form>' +
  "<script>document.forms[0].submit()</script></body></html>";

/** POST /checkout's answer for an unpaid plan: the ECPay auto-submit checkout page. */
function checkoutResponse() {
  return new Response(CHECKOUT_HTML, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

/** A saved row. Omit `status` for a legacy M1 row with no subscription_status. */
function savedRow(plan_name: string, route: string, target_price: number, status?: string) {
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
    ...(status ? { subscription_status: status, merchant_trade_no: "FP2610231200001A2B3C" } : {}),
  };
}

let documentWrites: string[];
let documentCalls: string[];

type Reply = Response | Error | (() => Response | Promise<Response>);

/**
 * Route fetch calls: GET /subscriptions → `list` (default: none), POST /subscribe →
 * `subscribe`, POST /checkout → `checkout`, POST /cancel → `cancel`.
 */
function mockApi({
  list = [],
  subscribe,
  checkout,
  cancel,
}: {
  list?: unknown[] | Reply;
  subscribe?: Reply;
  checkout?: Reply;
  cancel?: Reply;
}) {
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
    if (url.endsWith("/checkout")) return reply(checkout);
    if (url.endsWith("/cancel")) return reply(cancel);
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

function checkoutRequests() {
  return (fetchMock.mock.calls as [string, RequestInit][]).filter(([url]) =>
    url.endsWith("/checkout"),
  );
}

/** The body of the `index`th save, whether it went to /subscribe or /checkout. */
function sentBody(index = 0) {
  const saves = (fetchMock.mock.calls as [string, RequestInit][]).filter(
    ([url]) => url.endsWith("/subscribe") || url.endsWith("/checkout"),
  );
  return JSON.parse(String(saves[index]?.[1].body));
}

beforeEach(() => {
  vi.stubEnv("VITE_FLIGHT_API_URL", API);
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  mockApi({});
  // The cashier handoff replaces the whole page; record it instead of letting jsdom do it.
  documentWrites = [];
  documentCalls = [];
  // document.open is overloaded; spy on the no-argument form the handoff uses.
  vi.spyOn(document as unknown as { open: () => Document }, "open").mockImplementation(() => {
    documentCalls.push("open");
    return document;
  });
  vi.spyOn(document, "write").mockImplementation((...html: string[]) => {
    documentCalls.push("write");
    documentWrites.push(html.join(""));
  });
  vi.spyOn(document, "close").mockImplementation(() => {
    documentCalls.push("close");
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Dashboard plan cards", () => {
  it("shows the Tokyo, Seoul and London plans with a current-fare hint", () => {
    renderDashboard();

    expect(within(card(/台北 ✈ 東京/)).getByText(/NT\$9,325/)).toBeInTheDocument();
    expect(within(card(/台北 ✈ 首爾/)).getByText(/NT\$5,989/)).toBeInTheDocument();
    expect(within(card(/台北 ✈ 倫敦/)).getByText(/NT\$30,443/)).toBeInTheDocument();
  });

  it("starts checkout for a plan by POSTing the email, plan and target to /checkout", async () => {
    mockApi({ checkout: checkoutResponse() });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "10000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));

    await waitFor(() => expect(checkoutRequests()).toHaveLength(1));
    const [url, init] = checkoutRequests()[0]!;
    expect(url).toBe(`${API}/checkout`);
    expect(init.method).toBe("POST");
    expect(sentBody()).toEqual({
      email: "amy@example.com",
      plan_name: "tokyo",
      target_price: 10000,
    });
  });

  it("hands the page to ECPay's cashier when /checkout answers with the checkout form", async () => {
    mockApi({ checkout: checkoutResponse() });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "10000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));

    await waitFor(() => expect(documentCalls).toEqual(["open", "write", "close"]));
    expect(documentWrites).toEqual([CHECKOUT_HTML]);
    expect(within(tokyo).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("sends the Seoul plan from the Seoul card", async () => {
    mockApi({ checkout: checkoutResponse() });
    renderDashboard();

    const seoul = card(/台北 ✈ 首爾/);
    fireEvent.change(within(seoul).getByLabelText(/目標價/), { target: { value: "5,500" } });
    fireEvent.click(within(seoul).getByRole("button", { name: "開始追蹤" }));

    await waitFor(() => expect(checkoutRequests()).toHaveLength(1));
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
    expect(checkoutRequests()).toHaveLength(0);
  });

  it("shows the API's message when the save is rejected", async () => {
    mockApi({ checkout: jsonResponse(400, { message: "Unknown plan_name" }) });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "10000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));

    expect(await within(tokyo).findByRole("alert")).toHaveTextContent(/Unknown plan_name/);
    expect(within(tokyo).getByRole("button", { name: "開始追蹤" })).toBeEnabled();
  });

  it("shows an error when the network fails, and keeps the other card usable", async () => {
    mockApi({ checkout: new TypeError("Failed to fetch") });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "10000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));

    expect(await within(tokyo).findByRole("alert")).toHaveTextContent(/訂閱失敗/);
    expect(within(card(/台北 ✈ 首爾/)).queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("Dashboard subscribed state", () => {
  it("loads the signed-in user's subscriptions and marks an active plan 已訂閱 (有效)", async () => {
    mockApi({ list: [savedRow("tokyo", "TPE-TYO", 10000, "active")] });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    expect(await within(tokyo).findByText("已訂閱 (有效)")).toBeInTheDocument();
    expect(within(tokyo).getByText(/目前目標.*NT\$10,000/)).toBeInTheDocument();
    expect(within(tokyo).getByLabelText(/目標價/)).toBeInTheDocument();
    expect(within(tokyo).getByRole("button", { name: "更新目標價" })).toBeInTheDocument();
    expect(within(tokyo).queryByText("未完成付款")).not.toBeInTheDocument();
    expect(within(tokyo).queryByRole("button", { name: "完成付款" })).not.toBeInTheDocument();
    expect(listRequests()[0]?.[0]).toBe(`${API}/subscriptions?email=amy%40example.com`);

    const seoul = card(/台北 ✈ 首爾/);
    expect(within(seoul).queryByText(/已訂閱/)).not.toBeInTheDocument();
    expect(within(seoul).getByRole("button", { name: "開始追蹤" })).toBeInTheDocument();
  });

  it.each([
    ["a pending_payment row", "pending_payment"],
    ["a legacy row with no status", undefined],
  ])("shows %s as 未完成付款 with a 完成付款 button", async (_label, status) => {
    mockApi({ list: [savedRow("tokyo", "TPE-TYO", 10000, status)] });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    expect(await within(tokyo).findByText("未完成付款")).toBeInTheDocument();
    expect(within(tokyo).queryByText(/已訂閱/)).not.toBeInTheDocument();
    expect(within(tokyo).getByText(/目前目標.*NT\$10,000/)).toBeInTheDocument();
    expect(within(tokyo).getByRole("button", { name: "完成付款" })).toBeInTheDocument();
    expect(within(tokyo).queryByRole("button", { name: "更新目標價" })).not.toBeInTheDocument();
  });

  it("完成付款 restarts checkout for the saved target and hands off to the cashier", async () => {
    mockApi({
      list: [savedRow("tokyo", "TPE-TYO", 10000, "pending_payment")],
      checkout: checkoutResponse(),
    });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.click(await within(tokyo).findByRole("button", { name: "完成付款" }));

    await waitFor(() => expect(documentWrites).toEqual([CHECKOUT_HTML]));
    expect(sentBody()).toEqual({
      email: "amy@example.com",
      plan_name: "tokyo",
      target_price: 10000,
    });
  });

  it("完成付款 uses a newly typed target when there is one", async () => {
    mockApi({
      list: [savedRow("tokyo", "TPE-TYO", 10000, "pending_payment")],
      checkout: checkoutResponse(),
    });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    await within(tokyo).findByText("未完成付款");
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "8000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "完成付款" }));

    await waitFor(() => expect(checkoutRequests()).toHaveLength(1));
    expect(sentBody().target_price).toBe(8000);
  });

  it("a JSON answer updates the card in place, without refetching the list", async () => {
    mockApi({
      list: [savedRow("tokyo", "TPE-TYO", 10000, "active")],
      subscribe: jsonResponse(200, savedRow("tokyo", "TPE-TYO", 8000, "active")),
    });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    await within(tokyo).findByText(/目前目標.*NT\$10,000/);
    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "8000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "更新目標價" }));

    expect(await within(tokyo).findByText(/目前目標.*NT\$8,000/)).toBeInTheDocument();
    expect(within(tokyo).queryByText(/目前目標.*NT\$10,000/)).not.toBeInTheDocument();
    expect(within(tokyo).getByText("已訂閱 (有效)")).toBeInTheDocument();
    expect(sentBody()).toEqual({
      email: "amy@example.com",
      plan_name: "tokyo",
      target_price: 8000,
    });
    expect(documentCalls).toEqual([]);
    expect(listRequests()).toHaveLength(1);
    expect(checkoutRequests()).toHaveLength(0); // a paid plan never goes back to the cashier
  });

  // The bug a stale backend caused: /checkout answering JSON for an unpaid plan used to look
  // like a successful save, so 完成付款 silently did nothing.
  it("完成付款 shows an error when the API answers without a checkout page", async () => {
    mockApi({
      list: [savedRow("tokyo", "TPE-TYO", 10000, "pending_payment")],
      checkout: jsonResponse(200, savedRow("tokyo", "TPE-TYO", 10000, "pending_payment")),
    });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.click(await within(tokyo).findByRole("button", { name: "完成付款" }));

    expect(await within(tokyo).findByRole("alert")).toHaveTextContent(/無法開啟付款頁面/);
    expect(documentCalls).toEqual([]);
  });

  it("完成付款 shows an error when the backend has no /checkout yet", async () => {
    mockApi({
      list: [savedRow("tokyo", "TPE-TYO", 10000, "pending_payment")],
      checkout: jsonResponse(404, { message: "Not Found" }),
    });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.click(await within(tokyo).findByRole("button", { name: "完成付款" }));

    expect(await within(tokyo).findByRole("alert")).toHaveTextContent(/訂閱失敗/);
    expect(documentCalls).toEqual([]);
  });

  it.each([
    ["the server errors", jsonResponse(500, { message: "boom" })],
    ["the network fails", new TypeError("Failed to fetch")],
  ])(
    "still renders every plan as unsubscribed, with a notice, when %s",
    async (_label, failure) => {
      mockApi({ list: failure, checkout: checkoutResponse() });
      renderDashboard();
      await waitFor(() => expect(listRequests()).toHaveLength(1));

      // A non-blocking notice explains why nothing shows as subscribed.
      const notice = await screen.findByRole("alert");
      expect(notice).toHaveTextContent(/無法載入你的訂閱/);

      for (const name of [/台北 ✈ 東京/, /台北 ✈ 首爾/, /台北 ✈ 倫敦/]) {
        expect(within(card(name)).queryByText(/已訂閱/)).not.toBeInTheDocument();
        expect(within(card(name)).getByRole("button", { name: "開始追蹤" })).toBeEnabled();
      }

      // Subscribing still works and goes to the cashier.
      const tokyo = card(/台北 ✈ 東京/);
      fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "9000" } });
      fireEvent.click(within(tokyo).getByRole("button", { name: "開始追蹤" }));
      await waitFor(() => expect(documentWrites).toEqual([CHECKOUT_HTML]));
    },
  );
});

describe("Dashboard purchase notice", () => {
  it("shows 付款成功 after returning from the cashier, and drops the query parameter", async () => {
    const router = renderDashboard("/app?purchase=success");

    expect(await screen.findByText(/付款成功，訂閱將在幾秒內生效/)).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toBe(""));
    expect(router.state.location.pathname).toBe("/app");
    // Still shown after the parameter is gone.
    expect(screen.getByText(/付款成功，訂閱將在幾秒內生效/)).toBeInTheDocument();
  });

  it("shows a failure notice for ?purchase=failed", async () => {
    const router = renderDashboard("/app?purchase=failed");

    expect(await screen.findByText(/付款未完成/)).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toBe(""));
  });

  it("can be dismissed", async () => {
    renderDashboard("/app?purchase=success");

    fireEvent.click(await screen.findByRole("button", { name: "關閉通知" }));

    expect(screen.queryByText(/付款成功/)).not.toBeInTheDocument();
  });

  it("shows nothing without the parameter", async () => {
    renderDashboard();
    await waitFor(() => expect(listRequests()).toHaveLength(1));

    expect(screen.queryByText(/付款成功|付款未完成/)).not.toBeInTheDocument();
  });
});

describe("Dashboard activation after paying", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** GET /subscriptions answers `pending` this many times, then the activated row. */
  function activationLandsAfter(pendingReplies: number) {
    let calls = 0;
    mockApi({
      list: () => {
        calls += 1;
        const status = calls <= pendingReplies ? "pending_payment" : "active";
        return jsonResponse(200, [savedRow("tokyo", "TPE-TYO", 10000, status)]);
      },
    });
  }

  it("fetches the list again after ?purchase=success until the card turns 已訂閱 (有效)", async () => {
    activationLandsAfter(2);
    renderDashboard("/app?purchase=success");

    const tokyo = card(/台北 ✈ 東京/);
    expect(await within(tokyo).findByText("未完成付款")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(10_000);

    expect(await within(tokyo).findByText("已訂閱 (有效)")).toBeInTheDocument();
    expect(listRequests()).toHaveLength(3);
    // Once active, it stops asking.
    await vi.advanceTimersByTimeAsync(30_000);
    expect(listRequests()).toHaveLength(3);
  });

  it("gives up after a while if the callback never arrives", async () => {
    activationLandsAfter(Infinity);
    renderDashboard("/app?purchase=success");
    await within(card(/台北 ✈ 東京/)).findByText("未完成付款");

    await vi.advanceTimersByTimeAsync(10 * 60_000);
    const settled = listRequests().length;
    expect(settled).toBeGreaterThan(2);
    expect(settled).toBeLessThanOrEqual(30);

    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(listRequests()).toHaveLength(settled);
  });

  it.each([
    ["without the parameter", "/app"],
    ["after ?purchase=failed", "/app?purchase=failed"],
  ])("does not poll an unpaid plan %s", async (_label, entry) => {
    activationLandsAfter(Infinity);
    renderDashboard(entry);
    await within(card(/台北 ✈ 東京/)).findByText("未完成付款");

    await vi.advanceTimersByTimeAsync(60_000);

    expect(listRequests()).toHaveLength(1);
  });
});

describe("Dashboard cancel and grace period", () => {
  /** A paid row with its period: `active` by default. */
  function paidRow(status: string, periodEnd: string, target = 10000) {
    return {
      ...savedRow("tokyo", "TPE-TYO", target, status),
      current_period_end: periodEnd,
      current_period_end_date: periodEnd.slice(0, 10),
    };
  }

  function cancelRequests() {
    return (fetchMock.mock.calls as [string, RequestInit][]).filter(([url]) =>
      url.endsWith("/cancel"),
    );
  }

  it("取消訂閱 asks first, then cancels and shows 已取消 · 有效至 the paid-through date", async () => {
    mockApi({
      list: [paidRow("active", "2999-11-24T03:05:00Z")],
      cancel: jsonResponse(200, {
        ...paidRow("cancelled", "2999-11-24T03:05:00Z"),
        cancelled_at: "2026-11-02T08:00:00Z",
      }),
    });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.click(await within(tokyo).findByRole("button", { name: "取消訂閱" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/有效至 2999-11-24/);
    expect(cancelRequests()).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "確認取消" }));

    expect(await within(tokyo).findByText("已取消 · 有效至 2999-11-24")).toBeInTheDocument();
    expect(within(tokyo).queryByText("已訂閱 (有效)")).not.toBeInTheDocument();
    expect(within(tokyo).queryByRole("button", { name: "取消訂閱" })).not.toBeInTheDocument();
    expect(within(tokyo).getByRole("button", { name: "更新目標價" })).toBeInTheDocument();
    const [url, init] = cancelRequests()[0] ?? [];
    expect(url).toBe(`${API}/cancel`);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ email: "amy@example.com", route: "TPE-TYO" });
    expect(listRequests()).toHaveLength(1);
  });

  it("keeps the subscription when the confirmation is dismissed", async () => {
    mockApi({ list: [paidRow("active", "2999-11-24T03:05:00Z")] });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.click(await within(tokyo).findByRole("button", { name: "取消訂閱" }));
    fireEvent.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "保留訂閱" }),
    );

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(cancelRequests()).toHaveLength(0);
    expect(within(tokyo).getByText("已訂閱 (有效)")).toBeInTheDocument();
  });

  it("shows the API's message when the cancel fails, and stays active", async () => {
    mockApi({
      list: [paidRow("active", "2999-11-24T03:05:00Z")],
      cancel: jsonResponse(502, { message: "Could not reach ECPay" }),
    });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    fireEvent.click(await within(tokyo).findByRole("button", { name: "取消訂閱" }));
    fireEvent.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "確認取消" }),
    );

    expect(await within(tokyo).findByText(/取消失敗：Could not reach ECPay/)).toBeInTheDocument();
    expect(within(tokyo).getByText("已訂閱 (有效)")).toBeInTheDocument();
    expect(within(tokyo).getByRole("button", { name: "取消訂閱" })).toBeInTheDocument();
  });

  it("a cancelled plan inside its paid period can still update its target, and can't be cancelled", async () => {
    mockApi({
      list: [paidRow("cancelled", "2999-11-24T03:05:00Z")],
      subscribe: jsonResponse(200, paidRow("cancelled", "2999-11-24T03:05:00Z", 8000)),
    });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    expect(await within(tokyo).findByText("已取消 · 有效至 2999-11-24")).toBeInTheDocument();
    expect(within(tokyo).queryByRole("button", { name: "取消訂閱" })).not.toBeInTheDocument();
    expect(within(tokyo).queryByText("已結束")).not.toBeInTheDocument();

    fireEvent.change(within(tokyo).getByLabelText(/目標價/), { target: { value: "8000" } });
    fireEvent.click(within(tokyo).getByRole("button", { name: "更新目標價" }));

    expect(await within(tokyo).findByText(/目前目標.*NT\$8,000/)).toBeInTheDocument();
    expect(within(tokyo).getByText("已取消 · 有效至 2999-11-24")).toBeInTheDocument();
    expect(documentCalls).toEqual([]);
  });

  it.each([
    ["an expired plan", "expired", "2020-01-01T00:00:00Z"],
    ["a cancelled plan past its paid period", "cancelled", "2020-01-01T00:00:00Z"],
    // The parser gets no alerts for it either (its paywall compares a missing date as past).
    ["a cancelled plan with no paid-through date", "cancelled", undefined],
  ])("shows %s as 已結束, and 重新訂閱 goes back through checkout", async (_label, status, end) => {
    const row = end ? paidRow(status, end) : savedRow("tokyo", "TPE-TYO", 10000, status);
    mockApi({ list: [row], checkout: checkoutResponse() });
    renderDashboard();

    const tokyo = card(/台北 ✈ 東京/);
    expect(await within(tokyo).findByText("已結束")).toBeInTheDocument();
    expect(within(tokyo).queryByText(/已取消/)).not.toBeInTheDocument();
    expect(within(tokyo).queryByText(/已訂閱/)).not.toBeInTheDocument();
    expect(within(tokyo).queryByRole("button", { name: "取消訂閱" })).not.toBeInTheDocument();

    fireEvent.click(within(tokyo).getByRole("button", { name: "重新訂閱" }));

    await waitFor(() => expect(documentWrites).toEqual([CHECKOUT_HTML]));
    expect(sentBody()).toEqual({
      email: "amy@example.com",
      plan_name: "tokyo",
      target_price: 10000,
    });
  });
});
