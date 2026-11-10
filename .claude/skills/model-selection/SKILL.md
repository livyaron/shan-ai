---
name: model-selection
description: Chooses the right LLM for a use case by matching task type, latency budget, and cost ceiling to candidate models, then wires in a switch-ready abstraction and a fallback chain. Use when picking or revisiting which model powers an AI feature.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# Pick the Right LLM for the Job

There is no single "best" model — there's the best model for *this* task at *this* budget and *this* latency. This skill turns model choice from a guess into a decision you can defend and revisit.

## In this repo (read first — wins over the generic steps below)

- **The abstraction already exists.** Every call goes through `app/services/llm_router.py` (per-usage provider + fallback, set by an admin in the DB, 30s cache). Model IDs live in exactly one place per provider: `groq_client.MODELS` (`openai/gpt-oss-120b` → `openai/gpt-oss-20b`) and `gemma_client.GEMMA_MODELS`. Never add a model ID anywhere else; a new AI feature = a new key in `llm_router.USAGE_LABELS`, not a new client.
- **Step 4 below is TypeScript for illustration only** — this backend is Python; extend the router, don't write `lib/llm.ts`.
- **Limits before choice:** Groq on_demand = 8,000 TPM counted as prompt + `max_tokens`; Gemma's thinking eats `maxOutputTokens`. See `.claude/rules/llm-providers.md` before changing a model or a budget.
- **No LLM computes a number** — evaluate candidates on prose/extraction quality only; numbers come from code.
- **Evals use synthetic fixtures** (`tests/`), never a local DB. Real-data checks happen in production only.
- Before sending user text to any candidate, replace `"` → `״` and `'` → `׳` (see CLAUDE.md).

## Step 1 — Characterize the task

Pin down what the feature actually demands:

- **Task type** — extraction, classification, summarization, reasoning, code, agentic tool use, long-context synthesis.
- **Quality bar** — does a wrong answer cost a refund, or just a retry?
- **Latency budget** — interactive (sub-second feel) vs. background batch.
- **Cost ceiling** — expected tokens per request × volume. Estimate before you pick.
- **Context size** — how much input must the model hold at once?

## Step 2 — Shortlist candidates

Map the requirements to 2–3 candidate models across tiers:

- **Frontier** when reasoning quality or agentic reliability is the bottleneck.
- **Mid-tier** for the everyday workhorse — good quality, much cheaper.
- **Small / fast** for high-volume, latency-sensitive, or simple tasks.
- **Open models** when you need self-hosting, data residency, or fine-tuning.

Compare on the axes that matter for your task — not just headline benchmarks.

## Step 3 — Run a quick eval

Don't pick on vibes. Assemble 20–50 representative inputs with known-good outputs and run each candidate. Score on accuracy, then break ties on cost and latency. A tiny eval set beats a leaderboard every time.

## Step 4 — Abstract the model behind an interface

Never hardcode a model ID across the codebase. Route every call through one module so swapping models is a one-line change:

```ts
// lib/llm.ts — single switchable entry point
export const MODELS = {
  primary: "claude-sonnet-4-6",
  fast: "claude-haiku-4-5",
  fallback: "gpt-5",
} as const

export async function complete(opts: { tier?: keyof typeof MODELS; messages: Message[] }) {
  const model = MODELS[opts.tier ?? "primary"]
  // ...dispatch to the right provider SDK
}
```

## Step 5 — Add a fallback chain

Production models have outages and rate limits. Define an ordered fallback (primary → secondary provider) and retry on 429/5xx so a single provider hiccup doesn't take the feature down.

## Step 6 — Monitor and revisit

Log model, tokens, latency, and cost per request. Models and prices change monthly — set a reminder to re-run your eval and re-price quarterly. The right choice in Q1 may not be the right choice in Q3.

## Checklist

- [ ] Task type, quality bar, latency, cost, and context size written down
- [ ] 2–3 candidates shortlisted across tiers
- [ ] Eval run on representative inputs — chosen on data, not vibes
- [ ] All calls routed through one switchable abstraction
- [ ] Fallback chain + retry on rate limits / outages
- [ ] Per-request cost & latency logged; revisit scheduled
