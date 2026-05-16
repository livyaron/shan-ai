---
name: update-manual
description: Autonomously update the Shan-AI user manual after major user-visible app changes. Detects what changed since the last `user-manual-vX.Y` tag — no user input needed for change description. TRIGGER on user requests like "update the manual", "refresh the docs", "regenerate the user manual", or after the user announces a major release. Do NOT trigger for tiny bug fixes or internal refactors that users never see — the skill itself filters non-user-visible diffs.
---

# Update the Shan-AI User Manual — Autonomous

The user manual lives at `docs/manual/`:

- `index.html` — single-page HTML, 9 chapters, embedded SVG infographics, Heebo font (Hebrew RTL).
- `sources/*.md` — per-chapter Markdown twins (used for NotebookLM uploads).
- `manual.pdf` — built artifact (also copied to `static/manual.pdf` so the login page serves it).
- `archive/manual-vX.Y.pdf` — every prior version, retained for diff/comparison.
- `render_pdf.sh` — one-shot Chromium renderer.

The original spec is `docs/superpowers/plans/2026-05-16-user-manual.md`. Chapter inventory:

| # | Chapter | HTML anchor | Markdown twin | Triggered by changes to |
|---|---------|-------------|----------------|-------------------------|
| 1 | Welcome + benefits | `<h1>1. ברוכים הבאים</h1>` | `sources/00_intro.md` | High-level architecture, new surfaces |
| 2 | Login + dashboard tour | `<h1>2. התחברות וסיור</h1>` | `sources/01_login_dashboard.md` | `app/templates/login.html`, navbar in any layout template |
| 3 | Ask page + decisions + RACI | `<h1>3. עמוד "שאל"</h1>` + `<h1>4. RACI</h1>` | `sources/02_ask.md` | `app/templates/ask.html`, `app/routers/ask.py`, `app/services/ask_router.py`, `app/services/claude_service.py`, `app/services/raci_service.py` |
| 5 | Telegram bot | `<h1>5. בוט הטלגרם</h1>` | `sources/03_telegram.md` | `app/services/telegram_polling.py`, `app/services/telegram_routing.py`, `app/routers/telegram.py` |
| 6 | Decisions list + approval | `<h1>6. רשימת ההחלטות</h1>` | `sources/04_decisions.md` | `app/templates/decisions.html`, `app/templates/decision_review.html`, `app/services/decision_service.py`, `app/services/distribution_service.py` |
| 7 | Learning loop | `<h1>7. איך המערכת לומדת</h1>` | `sources/05_learning_loop.md` | `app/templates/eval.html`, `app/templates/eval_curate.html`, `app/services/per_question_loop_service.py`, `app/services/gold_truth_service.py`, `app/services/answer_feedback_service.py` |
| 8 | Admin rules + FAQ + glossary | `<h1>8. ניהול כללים</h1>` + `<h1>9. שאלות נפוצות</h1>` | `sources/06_admin.md` | `app/templates/learning_rules.html`, `app/templates/learning.html`, `app/routers/learning_rules.py`, new fix-types, new models in `app/models.py` |

---

## Execution flow

The skill runs end-to-end with ZERO user input on the change description. It self-discovers what changed.

### Step 1: Find the last manual baseline

```bash
LAST_TAG=$(git tag -l "user-manual-v*" | sort -V | tail -1)
echo "Last manual tag: $LAST_TAG"
```

If no tag exists, baseline is the initial commit of `docs/manual/index.html` (find via `git log --reverse --format=%H -- docs/manual/index.html | head -1`).

### Step 2: Compute the changed-files set since baseline

```bash
git diff --name-only "$LAST_TAG..HEAD" | sort -u > /tmp/manual_diff.txt
wc -l /tmp/manual_diff.txt
head -30 /tmp/manual_diff.txt
```

If empty → nothing to update. Report "no changes since $LAST_TAG" and exit.

### Step 3: Classify which chapters are affected

For each line in `/tmp/manual_diff.txt`, map to chapter(s) per the table above. Use this exact mapping logic (run as a Bash script for determinism — `awk`/`grep` patterns):

```bash
AFFECTED=""
while IFS= read -r f; do
    case "$f" in
        app/templates/login.html|app/templates/dashboard.html) AFFECTED="$AFFECTED 2" ;;
        app/templates/ask.html|app/routers/ask.py|app/services/ask_router.py|app/services/claude_service.py|app/services/raci_service.py) AFFECTED="$AFFECTED 3" ;;
        app/services/telegram_polling.py|app/services/telegram_routing.py|app/routers/telegram.py) AFFECTED="$AFFECTED 5" ;;
        app/templates/decisions.html|app/templates/decision_review.html|app/services/decision_service.py|app/services/distribution_service.py) AFFECTED="$AFFECTED 6" ;;
        app/templates/eval.html|app/templates/eval_curate.html|app/services/per_question_loop_service.py|app/services/gold_truth_service.py|app/services/answer_feedback_service.py) AFFECTED="$AFFECTED 7" ;;
        app/templates/learning_rules.html|app/templates/learning.html|app/routers/learning_rules.py) AFFECTED="$AFFECTED 8" ;;
        app/models.py) AFFECTED="$AFFECTED 8" ;;
        app/services/knowledge_service.py|app/services/embedding_service.py) AFFECTED="$AFFECTED 3 7" ;;
    esac
done < /tmp/manual_diff.txt
AFFECTED=$(echo $AFFECTED | tr ' ' '\n' | sort -u | tr '\n' ' ')
echo "Affected chapters: $AFFECTED"
```

Files NOT in any case branch (tests/, docs/, migrations, .claude/, etc.) are ignored — they're not user-visible.

If `$AFFECTED` is empty after classification → nothing user-visible changed → report "no user-visible changes since $LAST_TAG" and exit without modifying anything.

### Step 4: For each affected chapter, summarize the diff and apply edits

For each chapter number N in `$AFFECTED`:

a) Identify the specific files that triggered chapter N (from the case statement match).

b) Read the diff for those files:
```bash
git diff "$LAST_TAG..HEAD" -- <file1> <file2> ... | head -400
```

c) Summarize the user-visible change in 1-3 sentences. Examples:
- "New 'הורד מדריך משתמש' download link added below the login form."
- "Decision-detection now runs BEFORE Q&A routing on /ask; an inline RACI confirm card appears for decision-shaped input."
- "Admin rules page added with 5 tabs: Aliases, Intent Overrides, Pins, Synonyms, Pending Approvals."

d) Locate the chapter in `docs/manual/index.html` (anchor in the table above) AND its `.md` twin. Update BOTH with the new behavior:
- Add/edit a paragraph, table row, step in the numbered list, or callout
- Keep the existing structure (don't restructure unless required)
- Hebrew RTL byte-for-byte
- If a new diagram is essential, add `<symbol id="diag-newname">...</symbol>` inside the existing `<svg>` `<defs>` block

e) If the change deletes a feature, REMOVE the corresponding text from both files.

### Step 5: Archive the previous PDF

```bash
mkdir -p docs/manual/archive
if [ -f docs/manual/manual.pdf ]; then
    PREV_TAG_VER=$(echo "$LAST_TAG" | sed 's/user-manual-//')
    cp docs/manual/manual.pdf "docs/manual/archive/manual-${PREV_TAG_VER}.pdf"
    echo "Archived prior PDF → docs/manual/archive/manual-${PREV_TAG_VER}.pdf"
fi
```

Each version is retained, so the user can diff PDFs over time.

### Step 6: Re-render the PDF

```bash
./docs/manual/render_pdf.sh
```

Verify output ~700KB–1.5MB. If much smaller, render failed — abort, restore HTML/MD from baseline, report error.

### Step 7: Sync the static copy

```bash
cp docs/manual/manual.pdf static/manual.pdf
```

The login page serves `/static/manual.pdf`. Without this copy, the download link still points to the previous version.

### Step 8: Bump the version tag

Compute next version. Read the last tag (`user-manual-v1.0`) and increment the MINOR for most changes, MAJOR for structural rewrites (new chapter or chapter removed):

```bash
LAST_VER=$(echo "$LAST_TAG" | sed 's/user-manual-v//')
LAST_MAJOR=$(echo "$LAST_VER" | cut -d. -f1)
LAST_MINOR=$(echo "$LAST_VER" | cut -d. -f2)
# Default: bump minor.
NEXT_VER="${LAST_MAJOR}.$((LAST_MINOR + 1))"
NEW_TAG="user-manual-v${NEXT_VER}"
echo "Next tag: $NEW_TAG"
```

If a new chapter was added or removed (the HTML's `<section class="page-break">` count changed), bump MAJOR instead: `NEXT_VER="$((LAST_MAJOR + 1)).0"`.

### Step 9: Commit + tag + push

```bash
# Compose commit message from the summaries collected in Step 4d.
COMMIT_MSG="docs(manual): autonomous update for $NEW_TAG

Affected chapters: $AFFECTED
Source diff range: $LAST_TAG..HEAD

Changes:
<bullet list of 1-3 sentence summaries from Step 4c, one per chapter>"

git add docs/manual/ static/manual.pdf
git commit -m "$COMMIT_MSG"
git tag "$NEW_TAG"
git push origin master --tags
```

### Step 10: Report

Output a 4-line summary:

```
Updated chapters: <list>
Diff range: <last_tag>..HEAD (<N> files, <M> non-test changes)
Archived: docs/manual/archive/manual-<prev>.pdf
New tag: user-manual-v<X.Y> · pushed
```

---

## Safety rails

- **Never run on a dirty working tree.** First: `git status --short` — if there are uncommitted changes that aren't `docs/manual/` or `static/manual.pdf`, abort and tell the user to commit/stash first.
- **If a chapter's content would shrink to nothing**, that signals a feature removal — confirm with the user before deleting the section entirely.
- **If render fails** (PDF size < 200KB or non-zero exit), revert manually-edited HTML/MD via `git checkout HEAD docs/manual/` and report the render error. Do NOT push a broken manual.
- **Telegram-only changes** (e.g., new `/command`) update only chapter 5. Don't touch other chapters.
- **`app/models.py` changes** affect chapter 8 ONLY if the new model is user-facing (referenced by an admin page or settings UI). Internal models like `RouteTrace` don't need a manual update.
- **Skill never asks the user "what changed"** — that's the whole point of the autonomous mode. If the diff is genuinely ambiguous (e.g., a renamed field appears in 4 places with different meanings), the skill should pick the most user-visible interpretation and note it in the commit message.

---

## Constraints (carried over from v1)

- Brand color palette is fixed — never change CSS variables without explicit approval.
- Chapter ordering and numbering are fixed — never reorder or renumber.
- Hebrew RTL is mandatory.
- HTML and `.md` twin must stay in lockstep — never update one without the other.

---

## Example end-to-end

A typical run (after the user added a "Download Manual" link to the login page):

```
$ /update-manual
Last manual tag: user-manual-v1.0
Diff range: user-manual-v1.0..HEAD (3 files, 2 user-visible)
  app/templates/login.html → chapter 2
  static/manual.pdf → ignored (artifact)
  .claude/skills/update-manual.md → ignored (skill)
Affected chapters: 2

Reading diff for chapter 2:
  app/templates/login.html added a 'manual-dl' button below the login form
  pointing to /static/manual.pdf with the Hebrew label "הורד מדריך משתמש".

Editing docs/manual/index.html chapter 2 + sources/01_login_dashboard.md:
  Added a new bullet under "כניסה למערכת": "🔽 לחיצה על 'הורד מדריך משתמש'
  שולחת לך את המדריך הזה כ-PDF — ללא צורך בכניסה."

Archived docs/manual/manual.pdf → docs/manual/archive/manual-v1.0.pdf
Rendered new PDF: 851234 bytes
Synced static/manual.pdf
Committed: c5a8b34 docs(manual): autonomous update for user-manual-v1.1
Tagged: user-manual-v1.1
Pushed to origin/master with tags

Updated chapters: 2
Diff range: user-manual-v1.0..HEAD (3 files, 1 user-visible)
Archived: docs/manual/archive/manual-v1.0.pdf
New tag: user-manual-v1.1 · pushed
```
