import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { LogOut, Radar } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import { Button } from "@/components/ui/button";
import { SiteHeader } from "@/components/SiteHeader";
import { useAuthUser } from "@/components/RequireAuth";
import { usePageMeta } from "@/hooks/use-page-meta";

export default function AppPage() {
  usePageMeta("Dashboard — Flight Price Notifier", "Your flight price tracking dashboard.");
  const user = useAuthUser();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

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
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Hi {user.email}</h1>
        <div className="mt-8 rounded-2xl border border-border bg-card p-8">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent text-primary-glow">
            <Radar className="h-5 w-5" />
          </span>
          <p className="mt-5 text-lg">你的航線追蹤儀表板即將上線 — 下一個里程碑會加上訂閱航線的功能。</p>
          <p className="mt-2 text-sm text-muted-foreground">
            Your dashboard is coming soon. Route-subscription will be added in the next milestone.
          </p>
        </div>
      </main>
    </div>
  );
}
