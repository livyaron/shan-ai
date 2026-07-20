# Second Brain for Shan-AI — Research & Options

**Status:** Research only — no code or schema changes yet.
**Scope decision:** Shared org brain (one knowledge pool, everyone's contributions benefit everyone's answers).
**Goal:** Give the bot deep, growing, persistent knowledge about the projects (electrical substation infrastructure), beyond what file uploads and formal decisions capture today.

> **Council review (2026-07-19):** this doc was reviewed by three independent reviewer agents — architect (design vs. actual code), ops pragmatist (deploy/cost/failure modes), product critic (user value). Verdict: **3/3 approve with changes.** All findings are folded into this revision; the notable ones are marked ⚖️ inline.
>
> **Implementation status (2026-07-20):** Phases 0, 1a (A+F), 1b (E), 1c (G), 2 (B), and Option D are **implemented** — see `memory_service.py`, `dossier_service.py`, `extraction_service.py`, `session_summary_service.py`, `job_guard.py`. Option C (entity graph) remains deferred per its evidence gate: build only when `query_logs` show cross-cutting questions the flat layers keep failing.

---

## 1. What "second brain" means for Shan-AI

A second brain is a persistent organizational memory the bot maintains and draws on automatically:

- **Capture** — knowledge enters easily, at the moment it exists (a chat message, a decision, a document), not only via formal file upload.
- **Organize** — knowledge is stored with structure: what it's about (which project/person/equipment), who said it, when, and whether it's still true.
- **Retrieve** — every answer is enriched with the relevant memories without the user asking for them.
- **Maintain** — memories get updated, corrected, and expired; the brain doesn't rot.

For a decision-intelligence system this is a direct force multiplier: the quality of classification, RACI suggestions, and Q&A answers is bounded by what the system knows about the projects.

---

## 2. Inventory: the brain Shan-AI already has

A significant part of a second brain already exists. Any new work should extend these, not duplicate them.

| Layer | Tables / files | What it remembers | How it's retrieved |
|---|---|---|---|
| **Document memory** | `knowledge_files`, `knowledge_chunks` (`Vector(384)`); `app/services/knowledge_service.py` | Uploaded PDF/DOCX/XLSX/CSV, chunked (600 chars / 100 overlap) and embedded | `search_knowledge()` — 4-path hybrid: vector cosine, ILIKE keywords/phrases, label-targeted, cross-chunk WBS; Hebrew normalization + proper-noun boosting |
| **Structured project memory** | `projects`, `project_snapshots`; `app/services/project_sync.py`, `project_tools.py` | The master weekly-report file parsed into per-project rows (manager, stage, WBS…) | `ask_router` project-query path → `answer_project_query` |
| **Decision memory** | `decisions.embedding`; `app/services/decision_service.py` | Every decision, embedded at capture | `embedding_service.get_similar_decisions()` for classification calibration; decision-keyword path in `ask_router` |
| **Lessons memory** | `lessons_learned` (`Vector(384)`), `knowledge_summaries`; `app/services/lessons_service.py` | LLM-extracted lessons from completed decisions; aggregated summaries per decision type | `get_relevant_lessons()` cosine search — ⚖️ injected **only into decision-analysis prompts** (`decision_service.py:50-96`), never into Q&A answers |
| **Self-repair memory** | `query_synonyms`, `correction_pins`, `prompt_overrides`, `eval_gold_answers`, `project_aliases`, `intent_overrides` | What the system learned about *how to answer* (not domain facts) | Loaded into `ask_router.route()` dispatch |

**Shared plumbing to reuse:** `embedding_service.embed()` (FastEmbed multilingual MiniLM, 384 dims — the mandated size for every pgvector column), `ask_router.route()` (the single unified answer path — production, web `/ask`, and eval all flow through it), `QueryLog`/`RouteTrace` observability, and the `AnswerFeedback` → gold/repair loop.

---

## 3. Gap analysis

What a full second brain has that Shan-AI currently lacks:

1. **Conversational fact capture.** There is no way to just *tell* the bot something. Knowledge enters only via file upload or a formal decision. "The transformer at substation X was replaced last month" said in chat evaporates.
2. **Auto-extraction of durable facts.** Messages, decisions, and feedback contain facts nobody writes down twice. Nothing mines them.
3. **Entity-centric knowledge.** Chunks and lessons are flat text. "Everything we know about substation X" or "who has touched project Y and why" requires cross-source aggregation that doesn't exist.
4. **Episodic / session memory.** The bot has no continuity between conversations ("as we discussed last week…").
5. **Memory lifecycle.** No update/expire/conflict handling: if a fact changes (new project manager), old chunks still say the old name and can win retrieval.
6. **Provenance & trust.** Document chunks don't record who asserted a fact or how confident to be; there is no review flow for memory content (the review flow that exists is for *answers*, via gold/repair).

---

## 4. Option catalog

Each option: what it does, an example interaction, how it plugs into the existing stack, effort (S/M/L), and risks. They are composable — this is a menu, not an either/or.

### Option A — Explicit memory notes ("זכור ש…") — **the foundation**

**What:** Users teach the bot facts directly in Telegram. A message like
`‏זכור ש: דני אחראי על תחנת המשנה בחדרה` ("remember that: Danny is responsible for the Hadera substation")
is stored as a memory note, embedded, and from then on enriches every relevant answer. Companion commands: `‏מה אתה זוכר על חדרה?` (list memories about a topic), `‏שכח את זה` (forget/deactivate).

**How it plugs in:**
- New model `MemoryNote` in `app/models.py` — ⚖️ ship the **full** schema in Phase 1 (adding columns later means another manual `ALTER TABLE` startup entry): `content`, `embedding = Vector(384)`, `created_by_id → users.id`, `project_id` (nullable FK to `projects`), `tags` JSON, `status` VARCHAR default `'active'` (`active`/`pending`/`rejected` — VARCHAR, never a PG enum), `source` VARCHAR (`user_taught`/`auto_extracted`), `superseded_by_id`, `valid_until`, timestamps. One canonical retrieval predicate: `status = 'active' AND superseded_by_id IS NULL AND (valid_until IS NULL OR valid_until > now())`. Auto-creates via `Base.metadata.create_all` (the `missions` table precedent).
- **Capture — ⚖️ collision with the decision flow must be handled.** Free text currently runs `_ai_route_message` → decision classification with a confirm keyboard, and a fact like "הטרנספורמטור בחדרה הוחלף" is indistinguishable from a status update. Two capture paths: (1) the remember-intent check (`זכור`/`תזכור` prefix + LLM fallback) sits **before** decision routing in `telegram_polling.py`; (2) add a "🧠 שמור כעובדה" button to the existing decision-confirm keyboard — no magic prefix to learn, and soft phrasings that fell into the decision flow get rescued instead of evaporating. Gershayim quote-safety before any Groq call, as always.
- **Project linkage:** reuse the existing disambiguation machinery (`find_projects_by_identifier`, `ProjectAlias`, `normalize_hebrew`, the `_awaiting_disambiguation` inline buttons) at capture time. ⚖️ Set `project_id` only on an exact/high-confidence alias match — a *wrong* link is worse than null (it would poison the wrong project's dossier in Option E). On multi-match show the disambig buttons plus "כללי" (no project); confirm what was stored with an undo/forget button.
- **Retrieval — ⚖️ two defects the council caught in the original design:**
  1. Wiring memories only into `answer_with_full_context()` never fires for the flagship example: "מי אחראי על חדרה?" matches `_PROJECT_COUNT_TRIGGERS` (`telegram_routing.py:29-32`) and the project-alias pre-rules, routing to `project_tools.answer_project_query` (dispatch step 2) — it never reaches RAG. Fix: retrieve memory context **once in `ask_router.route()`** (shared helper) and inject it into the project and decision paths too. Rule of thumb: never a RAG-only context source.
  2. The real context budget is **6,000 chars** (`MAX_CONTEXT_CHARS`, `knowledge_service.py:2241` — the ~10k comment at line 2115 is stale) and truncation is a tail slice, so a memory block appended after 20 anchor chunks gets cut off in most real answers. Fix: **prepend** the memory block (the code already does this for phrase hits, lines 2113-2117) or give each source a fixed char budget.
- ⚖️ Cosine top-k alone is not enough: `get_relevant_lessons` has no similarity threshold, and over a small table top-k always returns k rows however unrelated — with authoritative framing ("נלמד מ־[user]") that pollutes answers. Add a tuned distance cutoff (validated via the eval-gold loop) plus an ILIKE keyword path over note content (mirroring `search_knowledge` Path B) for Hebrew prefix morphology ("בחדרה" vs "חדרה"). Note: this is a **new** pattern for `answer_with_full_context` — lessons never set the precedent there (see inventory).
- **Role gating:** ⚖️ VIEWER is already hard-gated read-only (`telegram_polling.py:703-707`) — exclude it from teaching; manager roles open-teach (heavier gating would kill adoption in a small trusted team); admin gets a lightweight notification on new notes.
- **Voice add-on:** only Document/TEXT handlers are registered today — a voice-note → transcribe → remember-intent front-end is the natural capture medium for crews standing in a substation. Small extension once A exists; photos of equipment/nameplates are explicitly deferred.
- A small list/manage view could reuse the `files.html` dashboard pattern.

**Effort:** S (retrieval wiring is slightly more than originally scoped due to the router-level injection). **Risks:** low — explicit capture means no wrong-extraction risk.

**Why first:** it's the missing input channel. Every other option (auto-extraction, dossiers, graph) produces or consumes the same `memory_notes`-style store, so this table becomes the second brain's spine.

### Option B — Auto-extracted facts (mem0-style)

**What:** A background pass (nightly, like `eval_cron`) sends recent messages/decisions to Groq with an extraction prompt: "list durable facts worth remembering (people, roles, equipment, constraints, dates)."

**How it plugs in:** same `memory_notes` table (schema already carries `status`/`source` from Phase 1). Extraction service modeled on `lessons_service.run_batch_extraction()`, which already does this shape of work for decisions — but note it is naturally idempotent (`Decision.id.notin_(extracted_ids)`); ⚖️ message extraction needs an explicit **high-water mark** (`last_processed_message_id` in `system_flags`) or every run re-sends the whole recent history to Groq. Schedule away from the 03:00 UTC eval cron and 6h batch-eval slots — `groq_client.py` has no client-side pacing and colliding jobs just trade 429s (which still burn TPD).

⚖️ **Dedup-by-cosine alone is not implementable as originally sketched.** Short template facts ("X אחראי על Y") embed near-identically across different X/Y, and negation ("דני כבר לא אחראי") barely moves cosine — a similarity-only rule merges distinct facts and treats contradictions as updates. Use cosine only for **candidate retrieval** (loose threshold), then an LLM adjudication step classifying SAME / UPDATE / CONTRADICTS / NEW with subject-entity comparison.

⚖️ **The review queue needs anti-pileup design** — the CRITICAL-decision approval flow works because a human is waiting on it; a pending memory blocks nobody, so with a single admin the queue only grows. Auto-activate high-confidence non-conflicting facts (marked `source='auto_extracted'` for provenance), send a *weekly batched* digest with bulk-approve inline buttons instead of per-fact pings, auto-expire pending items after ~30 days, and resolve contradictions recency-wins-with-undo rather than blocking on the admin.

⚖️ **Scope caveat:** the bot handles private chats only — the `messages` table contains what users already deliberately told the bot, which undercuts B's "facts nobody writes down twice" premise. The team's real chatter lives in Telegram groups the bot isn't in; group ingestion is considered-and-deferred (consent + noise implications).

**Effort:** M. **Risks:** wrong or trivial extractions polluting answers (mitigated above); Groq token cost scales with message volume — batch nightly, cap batch size; Hebrew extraction quality needs prompt iteration — the eval loop measures whether memories help or hurt gold scores.

### Option C — Project-entity knowledge graph (GraphRAG-lite)

**What:** Extract entities (substations, projects, people, equipment, contractors) and typed relations (*responsible_for*, *located_at*, *decided_on*, *supplies*) from decisions, documents, and memories into `entities` + `entity_relations` tables. Answers cross-cutting questions flat RAG struggles with: "who has been involved with substation X and in what capacity?", "which decisions touched transformer type Y across all projects?"

**How it plugs in:** entities embed with `Vector(384)` for fuzzy matching (Hebrew name variants — the `normalize_hebrew` / `ProjectAlias` machinery already solves half of this for projects). Retrieval becomes: match question → entities → walk relations → pull linked decisions/chunks/memories as context. A new `ask_router` path, analogous to `_is_project_query`.

**Effort:** L (extraction pipeline + graph maintenance + new retrieval path). **Risks:** highest complexity; relation extraction quality in Hebrew; graph staleness. **Recommendation:** defer until real cross-cutting questions show up in `query_logs` that the flat layers can't answer — the logs give an evidence-based trigger for this investment.

### Option D — Cross-session conversation memory

**What:** Periodically (or when a conversation goes idle) summarize each user's recent exchanges into a short rolling summary; inject it as context in their next session. Gives the bot continuity: follow-ups like "ומה לגבי השני?" work across days, and the bot can reference prior discussions.

**How it plugs in:** ⚖️ **the original premise was half-wrong** — `messages` stores only *inbound* user messages (`telegram_service.py:101-110`, no role/direction column); bot replies live only in a capped in-memory deque and in `QueryLog.ai_response` for Q&A. Summarizing one side of a conversation gives weak continuity, so this needs either persisting bot turns (new column/table) or summarizing from a `messages` + `query_logs` join. Add a `conversation_summaries` table (per user, rolling, no embedding — always injected whole for that user); summarization via `llm_router.llm_chat`, scheduled like the feedback scheduler.

**Effort:** S–M (not pure S, per above). **Risks:** low; modest token cost; per-user rather than shared-brain value (nice UX, less organizational knowledge).

### Option E — Project dossiers (living project summaries) — **directly serves "knowledge about my projects"**

**What:** An auto-maintained dossier per project: current status (from `projects`), recent decisions, extracted lessons, memory notes, and key document facts — regenerated when inputs change (new decision, master-file sync, new memory). Two consumers:
1. **RAG context:** for project questions, the dossier is a dense, always-current context block — better than hoping the right chunks win retrieval.
2. **Humans:** `‏תיק פרויקט חדרה` ("project dossier: Hadera") returns the full brief in Telegram; could also feed the 07:00 missions digest.

**How it plugs in:** extends the existing `knowledge_summaries` pattern (one summary row per decision_type → one dossier row per project). `project_dossiers` table keyed by `project_id`, regenerated by a service that aggregates from the tables above via Groq. Injected in `ask_router`'s project path alongside `answer_project_query`.

⚖️ **The naive "regenerate on change" token math collapses at this scale.** There are ~233 projects, and the weekly master-file sync updates essentially *every* project row (`project_sync.py:394-395`) — so a change-trigger means 233 dense Groq calls per upload, on a TPD budget the team already halved batch-eval frequency to protect (and 70b 429s at 12k TPM on big context). Design instead: a **dirty-flag queue** — sync/decision hooks mark dossiers dirty; regenerate only when a content hash of the inputs actually changed; **drip** K dossiers per interval (like the `batch=8` eval pattern); nightly stale-refresh capped to the N oldest.

**Effort:** M. **Risks:** token cost (bounded by the drip design above); dossier staleness between drips is acceptable — the structured `projects` row is always live and answers the time-sensitive fields.

### Option F — Memory hygiene layer (cross-cutting)

**What:** Lifecycle management so the brain stays trustworthy:
- **Provenance:** every memory records who/when/source (built into A's schema).
- **Supersession:** a new fact about the same subject deactivates the old one (`superseded_by_id`) instead of deleting — history preserved, retrieval clean.
- **Contradiction surfacing:** on insert, high-similarity active memories with conflicting content get flagged to an admin (inline-button resolution, like decision approval).
- **Expiry:** optional `valid_until` for known-temporary facts; nightly job deactivates expired ones.
- **Correction:** 👎 on an answer that used a memory routes to that memory for review — reusing the `AnswerFeedback` machinery.
- ⚖️ **Precedence vs. structured truth:** a taught "דני אחראי על חדרה" can contradict `projects.manager` from the master file — and project answers come from the `projects` table, so the two sources would disagree across answer paths. Rule: **the master file wins for fields it owns**; a conflicting memory note gets flagged to the admin (inline buttons) rather than silently coexisting.
- ⚖️ **Kill switch + rollback:** gate memory-context injection behind a `system_flags` toggle (table exists, `app/models.py`) so it can be disabled without a deploy; the `source` column makes bulk rollback one UPDATE (`SET status='rejected' WHERE source='auto_extracted'`); attribute every injected note (see §7 observability) so a bad answer traces to a specific note.

**Effort:** S–M spread across the other options. **Risk of skipping it:** this is what separates a second brain from a junk drawer — stale facts actively make answers worse. The minimal version (provenance + supersession + forget command + kill switch) should ship inside Option A.

### Option G — Snapshot diffs as temporal memory ⚖️ *(added by council review)*

**What:** The weekly master-file sync already writes per-project `project_snapshots`. Diffing consecutive snapshots yields **hallucination-free temporal facts** at near-zero token cost: "מנהל הפרויקט השתנה מ־X ל־Y בתאריך…", "השלב עבר מתכנון לביצוע". This is exactly the Zep temporal-validity idea (§5) implemented with data already in the DB — and it answers history questions ("מה השתנה בחדרה?", "מתי התחלף המנהל?") that neither the current `projects` row nor flat RAG can.

**How it plugs in:** a diff pass after `project_sync.sync_projects_file` writes change-facts into `memory_notes` (`source='snapshot_diff'`, linked `project_id`, dated) — no LLM needed for extraction, only field comparison. They then flow through the same retrieval as every other memory, and feed Option E dossiers a "recent changes" section for free.

**Effort:** S. **Risks:** minimal — deterministic extraction, no hallucination surface. The council ranks this **ahead of Option B** in value-per-effort.

---

## 5. Framework landscape (2026): build vs. adopt

| Framework | Approach | Fit for Shan-AI |
|---|---|---|
| **Mem0** (OSS, ~47k★) | Auto memory extraction over vector + graph + KV stores; supports custom LLMs and pgvector | Closest drop-in, but assumes its own extraction prompts (English-centric), embedder config, and memory schema — would bypass the Hebrew normalization, 384-dim FastEmbed mandate, and hybrid ILIKE search that make current retrieval work |
| **Zep / Graphiti** | Temporal knowledge graph — facts carry validity windows (knows *when* something was true) | The temporal-validity idea is worth stealing for Option F, but Zep Community Edition is deprecated (no full-featured self-hosting) and the managed service adds an external dependency to an otherwise self-contained stack |
| **Letta (MemGPT)** | OS-style memory paging: agent self-edits core memory blocks, pages to archival storage | Designed for long-running autonomous agents; Shan-AI's request/response bot doesn't match the model |
| **Cognee** | Graph + vector hybrid with a "cognify" ingestion pipeline, many retrieval modes | Interesting for Option C territory, but a heavy dependency for one feature |
| **LangMem** | Memory for the LangGraph ecosystem | Shan-AI doesn't use LangChain/LangGraph — no fit |

**Recommendation: build, don't adopt.** The honest comparison is that Shan-AI already implements the core of what these frameworks sell — embedding pipeline, vector store, hybrid retrieval, extraction jobs (`lessons_service`), and even a self-repair eval loop most frameworks lack. Every framework would fight the three load-bearing constraints: 384-dim FastEmbed embeddings, Hebrew normalization/gershayim handling, and the single `ask_router` path that keeps eval mirroring production. What's missing is a few tables and services in the house style, not a platform. Borrow the *concepts* (mem0's extract-dedup-update loop for B, Zep's temporal validity for F).

Sources: [Mem0/Zep/Graphiti/Letta/LangMem comparison](https://medium.com/@wasowski.jarek/i-compared-5-ai-agent-memory-systems-across-6-dimensions-none-wins-6a658335ed0a) · [8 memory systems compared (Vectorize)](https://vectorize.io/articles/best-ai-agent-memory-systems) · [Atlan 2026 framework roundup](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/) · [Particula hands-on tests](https://particula.tech/blog/agent-memory-frameworks-tested-mem0-zep-letta-cognee-2026)

---

## 6. Recommended roadmap

| Phase | What | Why this order |
|---|---|---|
| **0 (recommended)** ⚖️ | DB-side single-run job guard (`job_runs` claim row via `INSERT ... ON CONFLICT DO NOTHING`, or advisory lock) for all new background jobs | Deployment is now Railway-only, so the historical local+Railway double-run source is retired — but the guard is cheap insurance against brief redeploy overlap or an accidentally started second instance (the 07:00 digest double-send showed what that costs) |
| **1a** | Option A + minimal F (memory notes with full schema, provenance, supersession, forget, kill switch; retrieval injected at the `ask_router` level) | Opens the missing input channel; creates the spine table everything else uses |
| **1b (parallel)** ⚖️ | Option E (project dossiers, drip-regenerated) | Does **not** depend on A — it aggregates tables that already exist and is the surest payoff for "knowledge about my projects"; don't gate the sure payoff behind an adoption-dependent one |
| **1c (parallel)** ⚖️ | Option G (snapshot diffs) | S effort, zero hallucination risk, feeds both retrieval and dossiers; highest value-per-effort in the catalog |
| **2** | Option B (auto-extraction with adjudication + anti-pileup review flow) — gated by an eval comparison (gold set with memories on vs. off) before activation | Scales capture beyond what users remember to teach; needs Phase 1's store and hygiene to land safely |
| **3** | Option C (entity graph) — **only if** `query_logs` show cross-cutting questions the flat layers keep failing | Largest investment; wait for evidence |
| Anytime | Option D (session memory) | Independent, small, nice UX add-on (requires bot-turn persistence first) |

Each phase is independently shippable and measurable. ⚖️ Measurement notes from the council: (a) eval-gold scoring has a blind spot — gold answers freeze while memories supersede, so **supersession must invalidate/regenerate gold rows** whose answers used the superseded memory, or the eval will penalize newly-correct answers; (b) the gold set is backward-looking while memory's value is largely in never-asked questions — also track cheap operational metrics: notes/week (adoption), memory-hit rate via `RouteTrace`, and 👎-traced-to-a-memory rate (precision).

---

## 7. Integration & ops notes (for whoever implements)

- **New tables:** define as SQLAlchemy models in `app/models.py`; `Base.metadata.create_all` at startup (`app/main.py`) creates them automatically on deploy — the `missions` table is the precedent. No manual SQL needed on fresh deploys. Deployment is Railway-only; ad-hoc SQL goes through `psql "$RAILWAY_DATABASE_URL"` (CLAUDE.md §4).
- **Future columns on those tables:** idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the `main.py` startup block — a Railway deploy then applies them automatically; no separate manual run needed.
- **Embeddings:** every new vector column must be `Vector(384)`; always embed via `embedding_service.embed()`.
- **Status columns:** VARCHAR, never PostgreSQL enums (the `roleenum` gotcha).
- **Bot output:** prefix all messages with `‏` (RTL mark); replace straight quotes with gershayim before Groq calls (JSON safety).
- **Retrieval wiring:** new context sources go into `ask_router.route()` so web `/ask` and the eval loop inherit them for free — ⚖️ never a bot-only side path **and never a RAG-only context source** (project/decision paths must see memories too). Context budget is **6,000 chars** (`MAX_CONTEXT_CHARS`) with tail-slice truncation — prepend memory blocks or use per-source budgets. Memory retrieval must be non-fatal in the house style (`get_relevant_lessons` returns `[]` on any failure): empty/broken memory layer degrades to today's behavior, never errors.
- **Background jobs:** ⚖️ all new periodic work (extraction, dossier drip, expiry sweep, session summarizer) registers in `eval_cron`'s APScheduler — one place to see all jobs, one place to apply the Phase-0 single-run guard — never as ad-hoc `asyncio.create_task` startup tasks.
- **Token cost:** A, D, G are negligible; E is bounded by the drip design; B scales with message volume — batch nightly, cap batch size, high-water mark for idempotency. All Groq calls go through `llm_router.llm_chat` for the existing fallback behavior; schedule new jobs away from the eval-cron slots.
- **Observability:** ⚖️ logging memory hits is not free as originally implied — `RouteTrace.applied_rule_ids` is computed inside `route()` and the RAG path hardcodes `[]`. Pick one mechanism and thread it: either extend the `answer_with_full_context` return dict with `memory_note_ids` into `_finish`, or record them in `QueryLog.sources_used`. Required for the kill-switch/rollback story to be targeted.
