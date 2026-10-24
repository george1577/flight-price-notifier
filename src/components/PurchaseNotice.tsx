import { useState } from "react";
import { CircleAlert, CircleCheck, X } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { PurchaseOutcome } from "@/hooks/use-purchase-outcome";

/**
 * The notice for a purchase outcome. Activation itself arrives separately, from ECPay's
 * server-to-server callback, so the dashboard re-fetches the list meanwhile.
 */
export function PurchaseNotice({ outcome: initial }: { outcome: PurchaseOutcome | null }) {
  const [outcome, setOutcome] = useState(initial);

  if (outcome === null) return null;

  const success = outcome === "success";
  const Icon = success ? CircleCheck : CircleAlert;
  return (
    <Alert
      role="status"
      className={`mt-6 rounded-2xl border-2 border-foreground pr-12 text-foreground ${
        success ? "bg-leaf/40" : "border-dashed bg-marigold/30"
      }`}
    >
      <Icon className="h-4 w-4" />
      <AlertTitle>{success ? "付款成功" : "付款未完成"}</AlertTitle>
      <AlertDescription>
        {success
          ? "付款成功，訂閱將在幾秒內生效。"
          : "這次付款沒有完成，沒有扣款。你可以在方案卡片上按「完成付款」再試一次。"}
      </AlertDescription>
      <button
        type="button"
        aria-label="關閉通知"
        onClick={() => setOutcome(null)}
        className="absolute right-3 top-3 rounded-full p-1 hover:bg-foreground/10"
      >
        <X className="h-4 w-4" />
      </button>
    </Alert>
  );
}
