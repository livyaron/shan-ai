# Shan-AI User Manual — Source Files

This folder contains the Markdown source files for the user manual, split per chapter.

## Files

- `00_intro.md` — Chapter 1: Welcome + benefits
- `01_login_dashboard.md` — Chapter 2: Login + navigation
- `02_ask.md` — Chapters 3+4: Ask page + decisions + RACI
- `03_telegram.md` — Chapter 5: Telegram bot
- `04_decisions.md` — Chapter 6: Decisions list
- `05_learning_loop.md` — Chapter 7: Learning loop
- `06_admin.md` — Chapters 8+9: Admin rules + FAQ + glossary

## Using with NotebookLM (optional, for AI audio walkthrough)

1. Go to https://notebooklm.google.com and sign in with your Google account.
2. Create a new notebook.
3. Click "Add source" → "Upload files".
4. Select all `.md` files from this folder.
5. Wait ~1 minute for indexing.
6. In the "Studio" panel on the right, click "Generate Audio Overview".
   NotebookLM will produce a 5-12 minute Hebrew audio walkthrough you can share.
7. Optional: ask the notebook chat questions like "סכם איך עובד עמוד שאל" or
   "מה ההבדל בין INFO ל-CRITICAL" for grounded answers.

## Generating the PDF

- **Browser print (recommended):** Open `../index.html` in Chrome → Ctrl+P →
  Destination: "Save as PDF" → A4 portrait → Save.
- **Headless (one-shot):** Run `../render_pdf.sh` (requires weasyprint).
