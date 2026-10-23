// React Query hooks over the flight-api client. The per-user subscriptions list lives in the
// cache under `subscriptionsKey(email)`; a successful save upserts into that list so any
// reader of it (e.g. a `useSubscriptions(email)` query backed by GET /subscriptions) flips
// to the subscribed state without a refetch.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  listSubscriptions,
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

export function useSaveSubscription(email: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Omit<SubscribeInput, "email">) => saveSubscription({ ...input, email }),
    onSuccess: (saved) => {
      queryClient.setQueryData<Subscription[]>(subscriptionsKey(email), (list) =>
        upsertSubscription(list, saved),
      );
    },
  });
}
