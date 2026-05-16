---
name: update-manual
description: Update the Shan-AI user manual (docs/manual/index.html + sources/*.md + manual.pdf) when a major app change has shipped — new feature, renamed surface, changed flow, new UI page, new admin capability. TRIGGER on user requests like "update the manual", "refresh the docs", "regenerate the user manual", "the manual is out of date", or after the user announces a major release. Do NOT trigger for tiny bug fixes or internal refactors that users never see.
---

# Update the Shan-AI User Manual

The user manual lives at `docs/manual/`:

- `index.html` — single-page HTML, 9 chapters, embedded SVG infographics, Heebo font (Hebrew RTL).
- `sources/*.md` — per-chapter Markdown twins (used for NotebookLM uploads).
- `manual.pdf` — built artifact (also copied to `static/manual.pdf` so the login page can serve it).
- `render_pdf.sh` — one-shot Chromium renderer.

The original spec is `docs/superpowers/plans/2026-05-16-user-manual.md`. Chapter inventory:

1. Welcome + benefits — `sources/00_intro.md`
2. Login + dashboard tour — `sources/01_login_dashboard.md`
3. Ask page + decisions + RACI — `sources/02_ask.md`
4. Telegram bot — `sources/03_telegram.md`
5. Decisions list + approval flow — `sources/04_decisions.md`
6. Learning loop — `sources/05_learning_loop.md`
7. Admin rules + FAQ + glossary — `sources/06_admin.md`

## When to update

Run this skill ONLY when a user-visible change shipped. Examples that warrant an update:

- New page/route added (e.g., a new admin tab, a new endpoint exposed to users)
- An existing flow's UI changed substantially (button moved, label renamed)
- A new feature exposed to users (e.g., a new fix-type they can author manually)
- A new RACI rule, new decision type, new badge meaning
- A renamed nav item or a new keyboard shortcut

Do NOT update for:

- Internal refactors with no user-visible effect
- Bug fixes that restore previously-documented behavior
- Tests, CI, dependencies
- Anything that doesn't change what the user sees or clicks

If unsure, ask the user "what changed that users will notice?" before proceeding.

## How to update

### Step 1: Identify what changed

Run `git log --oneline -n 20` and read the user-facing commit messages. Ask the user to confirm what they consider the "major change" that triggered this update. Note WHICH chapters are affected — usually 1-3 chapters, rarely all 7.

### Step 2: Read the affected chapter section(s)

Use `Read` on `docs/manual/index.html` to locate the relevant `<section class="page-break">` block. Each chapter is wrapped by a `<!-- Chapter N -->` comment or starts with `<h1>N. ...</h1>`.

Also read the matching `docs/manual/sources/0X_*.md` twin.

### Step 3: Edit BOTH the HTML and the Markdown

This is critical: the HTML powers the PDF and the .md powers NotebookLM. Both must stay in sync.

- Use the `Edit` tool for surgical changes (one line, one table row, one bullet).
- Use the `Write` tool only for full-section rewrites (rare).
- Hebrew text byte-for-byte. Use the same SVG `<use href="#diag-...">` references for existing diagrams.

### Step 4: Add a new SVG infographic only if essential

If the change introduces a flow that needs visual explanation, add a new `<symbol id="diag-newname">...</symbol>` inside the existing `<svg>` `<defs>` block (near the top of `index.html`, right after the TOC). Then reference it from the relevant chapter via `<figure><svg width="100%" viewBox="..."><use href="#diag-newname"/></svg></figure>`.

Prefer extending an existing diagram (modify the SVG `<symbol>`) over adding a new one — keeps the visual language consistent.

### Step 5: Re-render the PDF

```bash
./docs/manual/render_pdf.sh
```

This produces `docs/manual/manual.pdf` via headless Chromium. Verify the new file size is reasonable (700KB – 1.5MB; if much smaller, render likely failed).

### Step 6: Sync the download artifact

```bash
cp docs/manual/manual.pdf static/manual.pdf
```

The login page serves `/static/manual.pdf`. Without this copy, the download link still points to the previous version.

### Step 7: Visual verification (manual gate)

Open `docs/manual/manual.pdf` in a viewer. Confirm:

- The updated chapter reads correctly
- Hebrew RTL still rendering
- Any new SVG diagram displays
- Page numbers + TOC still aligned
- No layout breakage in adjacent chapters

If broken: revert (`git checkout docs/manual/index.html`) and try a smaller, more targeted edit.

### Step 8: Commit + bump tag

```bash
git add docs/manual/ static/manual.pdf
git commit -m "docs(manual): update for <one-line description of the change>"
```

If this is a significant content addition (new chapter, multiple chapter changes, new feature documented), bump the tag:

```bash
# Existing tags: user-manual-v1.0
# Increment minor: v1.1, v1.2, ...
# Increment major: v2.0 if the manual structure changed
git tag user-manual-v1.1
git push origin master --tags
```

For small wording fixes, no tag — just commit.

## Constraints

- Do NOT change the manual's brand color palette without explicit user approval — colors match the live app and the user has visual expectations.
- Do NOT change the chapter ordering or numbering without asking — users may have shared page numbers with colleagues.
- Hebrew RTL is mandatory — never switch to LTR for any chapter.
- Keep the .md twins in lock-step with the HTML — NotebookLM uploads break otherwise.
- The skill runs against the production master branch by default. If working on a feature branch, ask the user whether to commit there or wait for merge.

## Reporting

After completing an update, report:

1. Which chapters were touched.
2. Whether the PDF was regenerated (size + path).
3. Whether the static copy was updated.
4. Commit SHA + tag (if bumped).

Brief — 3-5 lines. The user can read `git log` for detail.
