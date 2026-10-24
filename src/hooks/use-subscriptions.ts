// React Query hooks over the flight-api client. The per-user subscriptions list lives in the
// cache under `subscriptionsKey(email)`. A save either hands the page to ECPay's cashier
// (POST /checkout, unpaid plan) or upserts the updated row into that list (POST /subscribe,
// paid plan), so any reader of it shows the new target without a refetch.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelSubscription,
  isUnpaid,
  listSubscriptions,
  openCashier,
  saveTarget,
  startCheckout,
  type CheckoutResult,
  type SubscribeInput,
  type Subscription,
} from "@/lib/flight-api";
import type { Route } from "@/lib/plans";

export const subscriptionsKey = (email: string) => ["subscriptions", email] as const;

/** Replace the row for the same route, or append it. */
export function upsertSubscription(list: Subscription[] | undefined, saved: Subscription) {
  return [...(list ?? []).filter((s) => s.route !== saved.route), saved];
}

/** While awaiting activation, re-fetch this often, at most this many times (about 1 minute). */
export const ACTIVATION_POLL_MS = 3_000;
export const ACTIVATION_POLLS = 20;

/**
 * The signed-in user's subscriptions (GET /subscriptions). On failure the dashboard renders
 * every plan unsubscribed plus a notice.
 *
 * `awaitActivation`: the user just paid, but `active` arrives asynchronously from ECPay's
 * ReturnURL callback. Keep re-fetching while any plan is still unpaid, for a bounded time.
 */
export function useSubscriptions(email: string, { awaitActivation = false } = {}) {
  return useQuery({
    queryKey: subscriptionsKey(email),
    queryFn: () => listSubscriptions(email),
    enabled: email !== "",
    refetchInterval: (query) => {
      const { data, dataUpdateCount } = query.state;
      const waiting =
        awaitActivation && dataUpdateCount <= ACTIVATION_POLLS && data?.some(isUnpaid);
      return waiting ? ACTIVATION_POLL_MS : false;
    },
  });
}

export type SaveInput = Omit<SubscribeInput, "email"> & {
  /** true: go (back) through ECPay's cashier (POST /checkout). false: a paid plan's new target. */
  checkout: boolean;
};

/**
 * Save a target. A checkout hands the whole page to ECPay's cashier; a paid plan's in-place
 * update is upserted into the cached list.
 */
export function useSaveSubscription(email: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ checkout, ...input }: SaveInput): Promise<CheckoutResult> =>
      checkout
        ? startCheckout({ ...input, email })
        : { kind: "saved", subscription: await saveTarget({ ...input, email }) },
    onSuccess: (result) => {
      if (result.kind === "checkout") {
        openCashier(result.html);
        return;
      }
      queryClient.setQueryData<Subscription[]>(subscriptionsKey(email), (list) =>
        upsertSubscription(list, result.subscription),
      );
    },
  });
}

/**
 * Cancel a paid plan (POST /cancel) and upsert the returned `cancelled` row into the cached
 * list, so the card switches to 已取消 · 有效至 without a refetch.
 */
export function useCancelSubscription(email: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (route: Route) => cancelSubscription({ email, route }),
    onSuccess: (cancelled) => {
      queryClient.setQueryData<Subscription[]>(subscriptionsKey(email), (list) =>
        upsertSubscription(list, cancelled),
      );
    },
  });
}
