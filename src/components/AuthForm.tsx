import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { supabase } from "@/integrations/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SiteHeader } from "@/components/SiteHeader";
import { usePageMeta } from "@/hooks/use-page-meta";

const emailRedirectTo = () => window.location.origin + "/app";

// Supabase sends failed confirmation links back with the error in the URL hash, e.g. #error_code=otp_expired.
function linkErrorFromHash(hash: string): string | null {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  if (!params.get("error_code")) return null;
  return "確認連結已失效或已使用過，請輸入 email 重新寄送。This confirmation link has expired or was already used. Enter your email to get a new one.";
}

export function AuthForm({ mode }: { mode: "signin" | "signup" }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [checkEmail, setCheckEmail] = useState(false);
  const [linkError] = useState(() => linkErrorFromHash(location.hash));
  const [canResend, setCanResend] = useState(linkError !== null);
  const [resendStatus, setResendStatus] = useState<string | null>(null);
  const isSignUp = mode === "signup";
  usePageMeta(
    isSignUp ? "Sign up — Flight Price Notifier" : "Sign in — Flight Price Notifier",
    isSignUp
      ? "Create an account and get emailed when fares drop below your budget."
      : "Sign in to manage your flight price alerts.",
  );

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const { data, error } = isSignUp
      ? await supabase.auth.signUp({ email, password, options: { emailRedirectTo: emailRedirectTo() } })
      : await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) {
      if (error.code === "email_not_confirmed") setCanResend(true);
      return setError(error.message);
    }
    // Email confirmation is on, so sign-up returns no session until the link is clicked.
    if (!data.session) return setCheckEmail(true);
    navigate("/app");
  }

  async function resend() {
    if (!email) return setResendStatus("請先輸入 email。Enter your email first.");
    setResendStatus(null);
    setLoading(true);
    const { error } = await supabase.auth.resend({ type: "signup", email, options: { emailRedirectTo: emailRedirectTo() } });
    setLoading(false);
    setResendStatus(error ? error.message : `已重新寄送確認信到 ${email}。We sent a new confirmation link to ${email}.`);
  }

  return (
    <div className="min-h-screen bg-hero">
      <SiteHeader />
      <main className="mx-auto flex max-w-md flex-col px-5 py-16 animate-fade-up">
        <h1 className="text-3xl tracking-tight">{isSignUp ? "建立帳號 Sign up" : "登入 Sign in"}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {isSignUp ? "用 email 註冊，開始追蹤機票價格。" : "歡迎回來，登入查看你的航線。"}
        </p>
        {checkEmail ? (
          <div className="specimen mt-8 space-y-2 rounded-2xl p-6">
            <p className="font-semibold">請到信箱確認 Check your inbox</p>
            <p className="text-sm text-muted-foreground">
              我們已寄送確認信到 <span className="font-medium text-foreground">{email}</span>，點擊信中連結即可完成註冊。
              <br />
              We sent a confirmation link to {email}. Click it to finish signing up.
            </p>
            <Button type="button" variant="outline" size="sm" className="mt-2" onClick={resend} disabled={loading}>
              重新寄送 Resend email
            </Button>
            {resendStatus && <p className="text-sm text-muted-foreground">{resendStatus}</p>}
          </div>
        ) : (
        <form onSubmit={onSubmit} className="specimen mt-8 space-y-5 rounded-2xl p-6">
          {linkError && <p className="text-sm text-destructive">{linkError}</p>}
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
          {canResend && (
            <Button type="button" variant="outline" className="w-full" onClick={resend} disabled={loading}>
              重新寄送確認信 Resend confirmation email
            </Button>
          )}
          {resendStatus && <p className="text-sm text-muted-foreground">{resendStatus}</p>}
        </form>
        )}
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
