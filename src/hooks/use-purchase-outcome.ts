import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

export type PurchaseOutcome = "success" | "failed";

function readOutcome(value: string | null): PurchaseOutcome | null {
  return value === "success" || value === "failed" ? value : null;
}

/**
 * How the browser came back from ECPay's cashier: /app?purchase=success|failed (via the
 * flight-ecpay-result redirect), or null. The parameter is read once and then removed, so a
 * reload or a shared link doesn't see it again.
 */
export function usePurchaseOutcome() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [outcome] = useState(() => readOutcome(searchParams.get("purchase")));

  useEffect(() => {
    if (!searchParams.has("purchase")) return;
    setSearchParams(
      (params) => {
        params.delete("purchase");
        return params;
      },
      { replace: true },
    );
  }, [searchParams, setSearchParams]);

  return outcome;
}
