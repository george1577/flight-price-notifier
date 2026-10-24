import { useId, useState, type FormEvent } from "react";
import { Plane } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCancelSubscription, useSaveSubscription } from "@/hooks/use-subscriptions";
import { paymentState, type PaymentState, type Subscription } from "@/lib/flight-api";
import { formatTwd, parseTargetPrice, type Plan } from "@/lib/plans";

const SUBMIT_LABEL: Record<PaymentState | "none", string> = {
  none: "開始追蹤",
  unpaid: "完成付款",
  active: "更新目標價",
  grace: "更新目標價",
  ended: "重新訂閱",
};

const BADGE = "rounded-full border-2 border-foreground px-2.5 py-0.5 text-xs font-bold";

function StatusBadge({ state, subscription }: { state: PaymentState; subscription: Subscription }) {
  switch (state) {
    case "unpaid":
      return <span className={`${BADGE} bg-marigold`}>未完成付款</span>;
    case "active":
      return <span className={`${BADGE} bg-leaf`}>已訂閱 (有效)</span>;
    case "grace":
      return (
        <span className={`${BADGE} bg-background`}>
          已取消 · 有效至 {subscription.current_period_end_date}
        </span>
      );
    case "ended":
      return <span className={`${BADGE} bg-muted`}>已結束</span>;
  }
}

export function PlanCard({
  plan,
  email,
  subscription,
}: {
  plan: Plan;
  email: string;
  /** The saved row for this plan's route, if any (paid or not). */
  subscription?: Subscription | undefined;
}) {
  const headingId = useId();
  const inputId = useId();
  const [target, setTarget] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const save = useSaveSubscription(email);
  const cancel = useCancelSubscription(email);

  const state = subscription ? paymentState(subscription) : "none";
  // 完成付款 / 重新訂閱 re-run checkout; they can reuse the saved target without retyping it.
  const checkoutAgain = state === "unpaid" || state === "ended";

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const target_price =
      subscription && checkoutAgain && target.trim() === ""
        ? subscription.target_price
        : parseTargetPrice(target);
    if (target_price === null) {
      setValidationError("請輸入大於 0 的目標價（新台幣）。");
      return;
    }
    setValidationError(null);
    save.mutate({ plan_name: plan.name, target_price });
  }

  const error =
    validationError ??
    (save.isError
      ? `訂閱失敗：${save.error.message}`
      : cancel.isError
        ? `取消失敗：${cancel.error.message}`
        : null);
  const updated = save.isSuccess && save.data.kind === "saved" ? save.data.subscription : null;

  return (
    <section aria-labelledby={headingId} className="specimen rounded-2xl p-6">
      <div className="flex items-center gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-full border-2 border-foreground bg-leaf text-foreground">
          <Plane className="h-5 w-5" />
        </span>
        <div className="flex-1">
          <h2 id={headingId} className="text-xl tracking-tight">
            {plan.label}
          </h2>
          <p className="text-sm text-muted-foreground">
            目前最低價約 {formatTwd(plan.hintPriceTwd)}
          </p>
        </div>
        {subscription && state !== "none" && (
          <StatusBadge state={state} subscription={subscription} />
        )}
      </div>

      {subscription && (
        <p className="mt-4 text-sm">目前目標價：{formatTwd(subscription.target_price)}</p>
      )}

      <form onSubmit={onSubmit} noValidate className="mt-5 space-y-3">
        <div className="space-y-2">
          <Label htmlFor={inputId}>目標價（TWD）</Label>
          <Input
            id={inputId}
            inputMode="numeric"
            placeholder={String(plan.hintPriceTwd)}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            aria-invalid={validationError ? true : undefined}
          />
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {updated && !error && (
          <p className="text-sm text-muted-foreground">
            已更新目標 — {formatTwd(updated.target_price)}
          </p>
        )}
        <Button type="submit" variant="hero" className="w-full" disabled={save.isPending}>
          {SUBMIT_LABEL[state]}
        </Button>
      </form>

      {subscription && state === "active" && (
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="ghost" className="mt-3 w-full" disabled={cancel.isPending}>
              取消訂閱
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>取消 {plan.label} 的訂閱？</AlertDialogTitle>
              <AlertDialogDescription>
                之後不會再扣款。已付費期間內仍會收到降價通知
                {subscription.current_period_end_date
                  ? `，有效至 ${subscription.current_period_end_date}`
                  : ""}
                。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>保留訂閱</AlertDialogCancel>
              <AlertDialogAction onClick={() => cancel.mutate(subscription.route)}>
                確認取消
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </section>
  );
}
