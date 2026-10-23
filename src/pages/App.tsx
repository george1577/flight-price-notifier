import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { CloudOff, LogOut } from "lucide-react";
import { PlanCard } from "@/components/PlanCard";
import { PLANS } from "@/lib/plans";
import { supabase } from "@/integrations/supabase/client";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
  // If the list request fails, `data` stays undefined: every plan renders unsubscribed (still
  // usable) and a non-blocking notice says the saved subscriptions couldn't be loaded.
  const { data: subscriptions, isError: loadFailed } = useSubscriptions(email);

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
        {loadFailed && (
          <Alert className="mt-6 rounded-2xl border-2 border-dashed border-foreground bg-marigold/30 text-foreground">
            <CloudOff className="h-4 w-4" />
            <AlertTitle>無法載入你的訂閱</AlertTitle>
            <AlertDescription>
              已訂閱的航線暫時顯示為未訂閱。你仍可設定目標價，或稍後重新整理頁面。
            </AlertDescription>
          </Alert>
        )}
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
