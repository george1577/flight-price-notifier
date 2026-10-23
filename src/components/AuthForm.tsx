import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { supabase } from "@/integrations/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SiteHeader } from "@/components/SiteHeader";

export function AuthForm({ mode }: { mode: "signin" | "signup" }) {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const isSignUp = mode === "signup";

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const { error } = isSignUp
      ? await supabase.auth.signUp({ email, password, options: { emailRedirectTo: window.location.origin + "/app" } })
      : await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) return setError(error.message);
    navigate({ to: "/app" });
  }

  return (
    <div className="min-h-screen bg-hero">
      <SiteHeader />
      <main className="mx-auto flex max-w-md flex-col px-5 py-16 animate-fade-up">
        <h1 className="text-3xl font-bold tracking-tight">{isSignUp ? "建立帳號 Sign up" : "登入 Sign in"}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {isSignUp ? "用 email 註冊，開始追蹤機票價格。" : "歡迎回來，登入查看你的航線。"}
        </p>
        <form onSubmit={onSubmit} className="mt-8 space-y-5 rounded-2xl border border-border bg-card p-6">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" required autoComplete="email" placeholder="user@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" required minLength={6} autoComplete={isSignUp ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" variant="hero" className="w-full" disabled={loading}>
            {loading ? "請稍候…" : isSignUp ? "Sign up / 註冊" : "Sign in / 登入"}
          </Button>
        </form>
        <p className="mt-6 text-center text-sm text-muted-foreground">
          {isSignUp ? (
            <>已經有帳號？ <Link to="/signin" className="text-primary hover:underline">Sign in</Link></>
          ) : (
            <>還沒有帳號？ <Link to="/signup" className="text-primary hover:underline">Sign up</Link></>
          )}
        </p>
      </main>
    </div>
  );
}
