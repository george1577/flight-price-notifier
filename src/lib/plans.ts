// The fixed plans. The backend (lambdas/flight-save-subscription) holds the same table.
export type PlanName = "tokyo" | "seoul" | "london";
export type Route = "TPE-TYO" | "TPE-SEL" | "TPE-LON";

export type Plan = {
  name: PlanName;
  label: string;
  origin: "TPE";
  destination: "TYO" | "SEL" | "LON";
  route: Route;
  /** Rough current cheapest fare in TWD, shown as a budgeting hint. */
  hintPriceTwd: number;
};

export const PLANS: readonly Plan[] = [
  {
    name: "tokyo",
    label: "台北 ✈ 東京",
    origin: "TPE",
    destination: "TYO",
    route: "TPE-TYO",
    hintPriceTwd: 9325,
  },
  {
    name: "seoul",
    label: "台北 ✈ 首爾",
    origin: "TPE",
    destination: "SEL",
    route: "TPE-SEL",
    hintPriceTwd: 5989,
  },
  {
    name: "london",
    label: "台北 ✈ 倫敦",
    origin: "TPE",
    destination: "LON",
    route: "TPE-LON",
    hintPriceTwd: 30443,
  },
];

/** Parse a user-typed TWD amount ("10000", "10,000"). Returns null unless it is a positive number. */
export function parseTargetPrice(raw: string): number | null {
  const cleaned = raw.replace(/[,\s]/g, "");
  if (!/^\d+(\.\d+)?$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function formatTwd(amount: number) {
  return `NT$${amount.toLocaleString("en-US")}`;
}
