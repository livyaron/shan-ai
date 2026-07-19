# Second Brain for Shan-AI — Research & Options

**Status:** Research only — no code or schema changes yet.
**Scope decision:** Shared org brain (one knowledge pool, everyone's contributions benefit everyone's answers).
**Goal:** Give the bot deep, growing, persistent knowledge about the projects (electrical substation infrastructure), beyond what file uploads and formal decisions capture today.

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
| **Lessons memory** | `lessons_learned` (`Vector(384)`), `knowledge_summaries`; `app/services/lessons_service.py` | LLM-extracted lessons from completed decisions; aggregated summaries per decision type | `get_relevant_lessons()` cosine search; injected as context |
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
- New model `MemoryNote` in `app/models.py`: `content`, `embedding = Vector(384)`, `created_by_id → users.id`, `project_id` (nullable FK to `projects`), `tags` JSON, `is_active`, `superseded_by_id`, timestamps. Auto-creates via `Base.metadata.create_all` (the `missions` table precedent — no manual SQL on fresh deploys).
- Capture: a "remember" intent in `telegram_polling.py` (prefix match on `זכור`/`תזכור` plus an LLM fallback for softer phrasings), reusing the gershayim quote-safety rule before any Groq call.
- Retrieval: a new context source in `knowledge_service.answer_with_full_context()` — cosine top-k over active memory notes, formatted like `format_lessons_context()` does for lessons, with attribution ("נלמד מ־[user], [date]").
- A small list/manage view could reuse the `files.html` dashboard pattern.

**Effort:** S. **Risks:** low — explicit capture means no wrong-extraction risk; main design question is how aggressively to detect the "remember" intent vs. requiring the exact prefix.

**Why first:** it's the missing input channel. Every other option (auto-extraction, dossiers, graph) produces or consumes the same `memory_notes`-style store, so this table becomes the second brain's spine.

### Option B — Auto-extracted facts (mem0-style)

**What:** A background pass (per message batch or nightly, like `eval_cron`) sends recent messages/decisions to Groq with an extraction prompt: "list durable facts worth remembering (people, roles, equipment, constraints, dates)." Candidate facts are deduplicated against existing memories by cosine similarity; near-duplicates update the existing note, contradictions flag it. New facts land in a **review queue** (admin approves via inline buttons, mirroring the CRITICAL-decision approval flow) before becoming active.

**How it plugs in:** same `memory_notes` table plus a `status` VARCHAR (`pending`/`active`/`rejected` — VARCHAR, never a PG enum, per the `roleenum` gotcha) and `source` field (`user_taught` / `auto_extracted` / `decision`). Extraction service modeled on `lessons_service.run_batch_extraction()`, which already does exactly this shape of work for decisions.

**Effort:** M. **Risks:** wrong or trivial extractions polluting answers (mitigated by the review queue); Groq token cost scales with message volume; Hebrew extraction quality needs prompt iteration — the existing eval loop can measure whether memories help or hurt gold-answer scores.

### Option C — Project-entity knowledge graph (GraphRAG-lite)

**What:** Extract entities (substations, projects, people, equipment, contractors) and typed relations (*responsible_for*, *located_at*, *decided_on*, *supplies*) from decisions, documents, and memories into `entities` + `entity_relations` tables. Answers cross-cutting questions flat RAG struggles with: "who has been involved with substation X and in what capacity?", "which decisions touched transformer type Y across all projects?"

**How it plugs in:** entities embed with `Vector(384)` for fuzzy matching (Hebrew name variants — the `normalize_hebrew` / `ProjectAlias` machinery already solves half of this for projects). Retrieval becomes: match question → entities → walk relations → pull linked decisions/chunks/memories as context. A new `ask_router` path, analogous to `_is_project_query`.

**Effort:** L (extraction pipeline + graph maintenance + new retrieval path). **Risks:** highest complexity; relation extraction quality in Hebrew; graph staleness. **Recommendation:** defer until real cross-cutting questions show up in `query_logs` that the flat layers can't answer — the logs give an evidence-based trigger for this investment.

### Option D — Cross-session conversation memory

**What:** Periodically (or when a conversation goes idle) summarize each user's recent exchanges into a short rolling summary; inject it as context in their next session. Gives the bot continuity: follow-ups like "ומה לגבי השני?" work across days, and the bot can reference prior discussions.

**How it plugs in:** `messages` table already stores the raw history; add a `conversation_summaries` table (per user, rolling, no embedding needed — always injected whole for that user). Summarization via `llm_router.llm_chat`, scheduled like the feedback scheduler.

**Effort:** S. **Risks:** low; modest token cost; per-user rather than shared-brain value (nice UX, less organizational knowledge).

### Option E — Project dossiers (living project summaries) — **directly serves "knowledge about my projects"**

**What:** An auto-maintained dossier per project: current status (from `projects`), recent decisions, extracted lessons, memory notes, and key document facts — regenerated when inputs change (new decision, master-file sync, new memory). Two consumers:
1. **RAG context:** for project questions, the dossier is a dense, always-current context block — better than hoping the right chunks win retrieval.
2. **Humans:** `‏תיק פרויקט חדרה` ("project dossier: Hadera") returns the full brief in Telegram; could also feed the 07:00 missions digest.

**How it plugs in:** extends the existing `knowledge_summaries` pattern (one summary row per decision_type → one dossier row per project). `project_dossiers` table keyed by `project_id`, regenerated by a service that aggregates from the tables above via Groq, triggered from `project_sync.sync_projects_file` and decision-completion hooks. Injected in `ask_router`'s project path alongside `answer_project_query`.

**Effort:** M. **Risks:** regeneration token cost (bounded: only on change, one project at a time); dossier drift if a trigger is missed (mitigate with a nightly refresh in `eval_cron`'s slot).

### Option F — Memory hygiene layer (cross-cutting)

**What:** Lifecycle management so the brain stays trustworthy:
- **Provenance:** every memory records who/when/source (built into A's schema).
- **Supersession:** a new fact about the same subject deactivates the old one (`superseded_by_id`) instead of deleting — history preserved, retrieval clean.
- **Contradiction surfacing:** on insert, high-similarity active memories with conflicting content get flagged to an admin (inline-button resolution, like decision approval).
- **Expiry:** optional `valid_until` for known-temporary facts; nightly job deactivates expired ones.
- **Correction:** 👎 on an answer that used a memory routes to that memory for review — reusing the `AnswerFeedback` machinery.

**Effort:** S–M spread across the other options. **Risk of skipping it:** this is what separates a second brain from a junk drawer — stale facts actively make answers worse. The minimal version (provenance + supersession + forget command) should ship inside Option A.

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
| **1** | Option A + minimal F (memory notes with provenance, supersession, forget command; retrieval wired into `answer_with_full_context`) | Opens the missing input channel; creates the spine table everything else uses; smallest risk, immediate value |
| **2** | Option E (project dossiers) | Biggest direct payoff for "knowledge about my projects"; builds on Phase 1 memories as an input |
| **3** | Option B (auto-extraction with admin review queue) | Scales capture beyond what users remember to teach; needs Phase 1's store and hygiene to land safely |
| **4** | Option C (entity graph) — **only if** `query_logs` show cross-cutting questions the flat layers keep failing | Largest investment; wait for evidence |
| Anytime | Option D (session memory) | Independent, small, nice UX add-on |

Each phase is independently shippable and measurable: add gold questions that require memory/dossier knowledge and let the existing eval loop score whether the new layer actually improves answers.

---

## 7. Integration & ops notes (for whoever implements)

- **New tables:** define as SQLAlchemy models in `app/models.py`; `Base.metadata.create_all` at startup (`app/main.py`) creates them automatically on both local and Railway — the `missions` table is the precedent. No manual SQL needed on fresh deploys.
- **Future columns on those tables:** idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the `main.py` startup block, run on **both** local Docker and Railway (see CLAUDE.md §4 for the pattern).
- **Embeddings:** every new vector column must be `Vector(384)`; always embed via `embedding_service.embed()`.
- **Status columns:** VARCHAR, never PostgreSQL enums (the `roleenum` gotcha).
- **Bot output:** prefix all messages with `‏` (RTL mark); replace straight quotes with gershayim before Groq calls (JSON safety).
- **Retrieval wiring:** new context sources go into `ask_router.route()` / `answer_with_full_context()` so web `/ask` and the eval loop inherit them for free — never a bot-only side path.
- **Token cost:** A and D are negligible; E is bounded (regenerate only on change); B scales with message volume — batch nightly and cap batch size. All Groq calls go through `llm_router.llm_chat` for the existing fallback behavior.
- **Observability:** log memory hits in `RouteTrace` so the eval loop and dashboard can see when memories influenced an answer.
