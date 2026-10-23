import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { PlanCard } from "@/components/PlanCard";
import { PLANS } from "@/lib/plans";
import { supabase } from "@/integrations/supabase/client";
import { Button } from "@/components/ui/button";
import { SiteHeader } from "@/components/SiteHeader";
import { useAuthUser } from "@/components/RequireAuth";
import { usePageMeta } from "@/hooks/use-page-meta";
import { useSubscriptions } from "@/hooks/use-subscriptions";

export default function AppPage() {
  usePageMeta("Dashboard — Flight Price Notifier", "Your flight price tracking dashboard.");
  const user = useAuthUser();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const email = user.email ?? "";
  // If the list request fails, `data` stays undefined and every plan renders unsubscribed.
  const { data: subscriptions } = useSubscriptions(email);

  async function signOut() {
    await queryClient.cancelQueries();
    queryClient.clear();
    await supabase.auth.signOut();
    navigate("/signin", { replace: true });
  }

  return (
    <div className="min-h-screen bg-hero">
      <SiteHeader
        right={
          <Button variant="outline" size="sm" onClick={signOut}>
            <LogOut /> Sign out
          </Button>
        }
      />
      <main className="mx-auto max-w-3xl px-5 py-20 animate-fade-up">
        <h1 className="break-all text-3xl tracking-tight sm:text-4xl">Hi {user.email}</h1>
        <p className="mt-3 text-muted-foreground">
          選一條航線、設定目標價，票價達標時我們會寄信通知你。
        </p>
        <div className="mt-8 grid gap-6 sm:grid-cols-2">
          {PLANS.map((plan) => (
            <PlanCard
              key={plan.name}
              plan={plan}
              email={email}
              subscription={subscriptions?.find((s) => s.route === plan.route)}
            />
          ))}
        </div>
      </main>
    </div>
  );
}
