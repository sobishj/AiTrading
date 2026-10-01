import type { ModelCostStats } from "../../services/types";

// Typical tokens per call for a model with no usage yet (mirrors the backend's /llm/cost-stats fallback).
const TYPICAL_IN = 2500;
const TYPICAL_OUT = 450;

interface CostEstimateProps {
  cap: number;
  stats?: ModelCostStats;
  local: boolean;
  /** Prices typed in the form (USD per 1M tokens); they override the saved/list price. */
  inputPrice: string;
  outputPrice: string;
}

const inr = (usd: number, rate: number) => `₹${Math.round(usd * rate).toLocaleString("en-IN")}`;
const money = (usd: number, rate: number | null) =>
  rate ? `${inr(usd, rate)} ($${usd < 1 ? usd.toFixed(2) : usd.toFixed(1)})` : `$${usd.toFixed(2)}`;

/**
 * What the daily cap means in money, worked out from this model's own measured tokens per call and its
 * price (or typical values before it has been used), updating as the cap is typed.
 */
export default function CostEstimate({ cap, stats, local, inputPrice, outputPrice }: CostEstimateProps) {
  if (local || stats?.is_local) {
    return <span className="block text-[11px] text-neon-emerald">Free — runs on this PC.</span>;
  }
  const typedIn = parseFloat(inputPrice);
  const typedOut = parseFloat(outputPrice);
  const price = !isNaN(typedIn) || !isNaN(typedOut)
    ? { input: isNaN(typedIn) ? 0 : typedIn, output: isNaN(typedOut) ? 0 : typedOut, source: "the prices below" }
    : stats?.price ?? null;
  if (!price) {
    return <span className="block text-[11px] text-amber-300">Enter the prices below to see what this cap costs.</span>;
  }
  const tin = stats?.tokens_per_call.input ?? TYPICAL_IN;
  const tout = stats?.tokens_per_call.output ?? TYPICAL_OUT;
  const measured = stats?.tokens_per_call.measured_from_calls ?? 0;
  const perCall = (tin / 1e6) * price.input + (tout / 1e6) * price.output;
  const rate = stats?.fx?.rate ?? null;
  const basis = measured
    ? `${tin.toLocaleString("en-IN")} in / ${tout.toLocaleString("en-IN")} out tokens per call, measured from ${measured} calls`
    : `about ${tin.toLocaleString("en-IN")} in / ${tout.toLocaleString("en-IN")} out tokens per call (typical — not measured yet)`;
  const recent = stats?.recent_usd_per_day;
  return (
    <span className="block text-[11px] text-slate-400 leading-snug"
      title={`Price: $${price.input}/$${price.output} per 1M tokens in/out (${price.source}). Based on ${basis}.${
        rate ? ` ₹ at USD/INR ${rate.toFixed(2)}.` : ""} Only AiTrading's own calls.`}>
      {cap > 0
        ? <>If all {cap} requests are used: <span className="text-slate-200">≈ {money(cap * perCall, rate)} a day</span></>
        : <>No cap — about {money(perCall, rate)} per request, with no daily limit</>}
      {recent != null && recent > 0 && <> · recently ≈ {money(recent, rate)} a day ({stats!.recent_calls_per_day} calls)</>}
      {!measured && <span className="text-slate-500"> · estimate until it has been used</span>}
    </span>
  );
}
