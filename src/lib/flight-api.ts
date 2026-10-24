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
  const status = subscription.subscription_status;
  return status === undefined || status === "pending_payment";
}

/**
 * POST /subscribe's answer: either ECPay's auto-submit checkout page (`text/html`), or the
 * row updated in place (`application/json`) for a plan that is already paid.
 */
export type SubscribeResult =
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
 * POST /subscribe: save the target for (email, route). An unpaid plan comes back as ECPay's
 * checkout page (hand it to `openCashier`); a paid plan comes back as the updated row.
 */
export async function saveSubscription(input: SubscribeInput): Promise<SubscribeResult> {
  const response = await send("/subscribe", {
    method: "POST",
    body: JSON.stringify(input),
  });
  if ((response.headers.get("content-type") ?? "").includes("text/html")) {
    return { kind: "checkout", html: await response.text() };
  }
  return { kind: "saved", subscription: (await response.json()) as Subscription };
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
