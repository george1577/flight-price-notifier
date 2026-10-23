import { useId, useState, type FormEvent } from "react";
import { Plane } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSaveSubscription } from "@/hooks/use-subscriptions";
import type { Subscription } from "@/lib/flight-api";
import { formatTwd, parseTargetPrice, type Plan } from "@/lib/plans";

export function PlanCard({
  plan,
  email,
  subscription,
}: {
  plan: Plan;
  email: string;
  /** The saved row for this plan's route, if the user is subscribed. */
  subscription?: Subscription | undefined;
}) {
  const headingId = useId();
  const inputId = useId();
  const [target, setTarget] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const save = useSaveSubscription(email);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const target_price = parseTargetPrice(target);
    if (target_price === null) {
      setValidationError("請輸入大於 0 的目標價（新台幣）。");
      return;
    }
    setValidationError(null);
    save.mutate({ plan_name: plan.name, target_price });
  }

  const error = validationError ?? (save.isError ? `訂閱失敗：${save.error.message}` : null);

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
        {subscription && (
          <span className="rounded-full border-2 border-foreground bg-leaf px-2.5 py-0.5 text-xs font-bold">
            已訂閱
          </span>
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
        {save.isSuccess && !error && (
          <p className="text-sm text-muted-foreground">
            已開始追蹤 — 目標 {formatTwd(save.data.target_price)}
          </p>
        )}
        <Button type="submit" variant="hero" className="w-full" disabled={save.isPending}>
          {subscription ? "更新目標價" : "開始追蹤"}
        </Button>
      </form>
    </section>
  );
}
