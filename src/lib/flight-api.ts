// Client for the flight-api HTTP API (API Gateway). The browser holds no AWS credentials:
// this public HTTP API is the only backend it talks to for app data.
import type { PlanName, Route } from "@/lib/plans";

/** A `subscriptions` row as returned by the API. M1 has no payment/status fields. */
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
};

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      body && typeof body.message === "string" ? body.message : `伺服器錯誤（${response.status}）`;
    throw new ApiError(message, response.status);
  }
  return body as T;
}

/**
 * GET /subscriptions?email=: every saved row for this user (empty list if none).
 * Known gap, accepted for M1: the API trusts this client-sent email; there is no JWT check.
 */
export function listSubscriptions(email: string) {
  return request<Subscription[]>(`/subscriptions?email=${encodeURIComponent(email)}`);
}

/** POST /subscribe: upsert the target for (email, route). Returns the saved row. */
export function saveSubscription(input: SubscribeInput) {
  return request<Subscription>("/subscribe", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
