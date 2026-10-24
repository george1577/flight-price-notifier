// React Query hooks over the flight-api client. The per-user subscriptions list lives in the
// cache under `subscriptionsKey(email)`. A save either hands the page to ECPay's cashier
// (unpaid plan) or upserts the updated row into that list (paid plan), so any reader of it
// shows the new target without a refetch.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listSubscriptions,
  openCashier,
  saveSubscription,
  type SubscribeInput,
  type Subscription,
} from "@/lib/flight-api";

export const subscriptionsKey = (email: string) => ["subscriptions", email] as const;

/** Replace the row for the same route, or append it. */
export function upsertSubscription(list: Subscription[] | undefined, saved: Subscription) {
  return [...(list ?? []).filter((s) => s.route !== saved.route), saved];
}

/** The signed-in user's subscriptions (GET /subscriptions). On failure the dashboard renders every plan unsubscribed plus a notice. */
export function useSubscriptions(email: string) {
  return useQuery({
    queryKey: subscriptionsKey(email),
    queryFn: () => listSubscriptions(email),
    enabled: email !== "",
  });
}

/**
 * Save a target. An unpaid plan hands the whole page to ECPay's cashier; a paid plan's
 * in-place update is upserted into the cached list.
 */
export function useSaveSubscription(email: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Omit<SubscribeInput, "email">) => saveSubscription({ ...input, email }),
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
