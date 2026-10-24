// Client for the flight-api HTTP API (API Gateway). The browser holds no AWS credentials:
// this public HTTP API is the only backend it talks to for app data.
import type { PlanName, Route } from "@/lib/plans";

/**
 * Where a subscription is in the ECPay lifecycle. Only ECPay's callbacks set `active`.
 * Legacy M1 rows have no status; treat them as `pending_payment` (see `isUnpaid`).
 */
export type SubscriptionStatus = "pending_payment" | "active" | "cancelled" | "expired";

/** A `subscriptions` row as returned by the API. The M2 payment attributes are optional. */
export type Subscription = {
  email: string;
  route: Route;
  plan_name: PlanName;
  origin: string;
  destination: string;
  target_price: number;
  currency: "TWD";
  created_at: string;
  updated_at: string;
  subscription_status?: SubscriptionStatus;
  /** The latest checkout attempt's ECPay MerchantTradeNo (the paid one once active). */
  merchant_trade_no?: string;
  period_type?: "M" | "D";
  frequency?: number;
  /** Paid-through instant, `YYYY-MM-DDTHH:MM:SSZ` (UTC). */
  current_period_end?: string;
  /** `YYYY-MM-DD`, for display. */
  current_period_end_date?: string;
  activated_at?: string;
  last_charged_at?: string;
  total_success_times?: number;
  failed_charge_count?: number;
  cancelled_at?: string;
};

/** Unpaid: never activated, or a legacy M1 row with no status. Alerts are off. */
export function isUnpaid(subscription: Subscription) {
  return paymentState(subscription) === "unpaid";
}

/**
 * What a plan card shows for a saved row (spec #16):
 * - `unpaid`: never activated (or legacy); 完成付款.
 * - `active`: paid and renewing; target form plus 取消訂閱.
 * - `grace`: cancelled, but the paid period hasn't ended; alerts continue, target editable.
 * - `ended`: expired, or cancelled past its paid period; 重新訂閱 goes back through checkout.
 */
export type PaymentState = "unpaid" | "active" | "grace" | "ended";

export function paymentState(subscription: Subscription, now: Date = new Date()): PaymentState {
  switch (subscription.subscription_status) {
    case "active":
      return "active";
    case "cancelled": {
      const end = Date.parse(subscription.current_period_end ?? "");
      // No period end counts as lapsed, as in the parser's paywall (it then gets no alerts).
      return end >= now.getTime() ? "grace" : "ended";
    }
    case "expired":
      return "ended";
    default:
      return "unpaid";
  }
}

/**
 * POST /checkout's answer: either ECPay's auto-submit checkout page (`text/html`), or, for a
 * plan that turned out to be paid already, the row updated in place (`application/json`).
 */
export type CheckoutResult =
  { kind: "checkout"; html: string } | { kind: "saved"; subscription: Subscription };

export type SubscribeInput = {
  email: string;
  plan_name: PlanName;
  target_price: number;
};

/** A failed API call. `message` is safe to show to the user. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function apiBaseUrl() {
  const base = import.meta.env["VITE_FLIGHT_API_URL"] as string | undefined;
  if (!base) throw new ApiError("尚未設定 API 位址（VITE_FLIGHT_API_URL）。");
  return base.replace(/\/+$/, "");
}

/** fetch() an API path. Throws ApiError on a network failure or a non-2xx answer. */
async function send(path: string, init?: RequestInit): Promise<Response> {
  const url = `${apiBaseUrl()}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { "content-type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError("無法連線到伺服器，請稍後再試。");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message =
      body && typeof body.message === "string" ? body.message : `伺服器錯誤（${response.status}）`;
    throw new ApiError(message, response.status);
  }
  return response;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await send(path, init);
  return (await response.json().catch(() => null)) as T;
}

/**
 * GET /subscriptions?email=: every saved row for this user (empty list if none).
 * Known gap, accepted for M1: the API trusts this client-sent email; there is no JWT check.
 */
export function listSubscriptions(email: string) {
  return request<Subscription[]>(`/subscriptions?email=${encodeURIComponent(email)}`);
}

/**
 * POST /subscribe: save the target and get the row back (JSON). Used for paid plans: it
 * never starts a checkout and never changes the payment state.
 */
export function saveTarget(input: SubscribeInput) {
  return request<Subscription>("/subscribe", { method: "POST", body: JSON.stringify(input) });
}

/**
 * POST /checkout: save the target and start an ECPay checkout. An unpaid plan comes back as
 * ECPay's checkout page (hand it to `openCashier`). A plan that is already paid comes back as
 * the updated row. Anything else (e.g. an unpaid row as JSON from an out-of-date backend) is
 * an error, so the button never silently does nothing.
 */
export async function startCheckout(input: SubscribeInput): Promise<CheckoutResult> {
  const response = await send("/checkout", {
    method: "POST",
    body: JSON.stringify(input),
  });
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("text/html")) {
    return { kind: "checkout", html: await response.text() };
  }
  const row = (await response.json().catch(() => null)) as Subscription | null;
  const state = row ? paymentState(row) : null;
  if (state === "active" || state === "grace") return { kind: "saved", subscription: row! };
  throw new ApiError("無法開啟付款頁面：伺服器沒有回傳付款頁（後端可能尚未部署最新版本）。");
}

/**
 * POST /cancel: stop the ECPay recurring order and mark the row `cancelled` (the paid
 * period is kept). Returns the updated row; cancelling twice returns it unchanged.
 * Same known gap as /subscribe: the API trusts the client-sent email.
 */
export function cancelSubscription(input: { email: string; route: Route }) {
  return request<Subscription>("/cancel", { method: "POST", body: JSON.stringify(input) });
}

/**
 * Replace this page with ECPay's checkout page. Its inline script auto-submits the form,
 * so the browser POSTs straight to the cashier.
 */
export function openCashier(html: string) {
  document.open();
  document.write(html);
  document.close();
}
