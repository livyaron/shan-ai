---
paths:
  - "app/services/groq_client.py"
  - "app/services/gemma_client.py"
  - "app/services/llm_router.py"
  - "app/services/llm_health.py"
  - "app/services/insight_ai.py"
  - "app/services/lessons_ai.py"
  - "app/services/claude_service.py"
  - "app/services/extraction_service.py"
  - "app/main.py"
  - "tests/test_groq_retry.py"
  - "tests/test_llm_*.py"
  - "tests/test_key_redaction.py"
  - "tests/test_insight_ai.py"
---
# LLM providers: Groq, Gemma, logging of keys

<!-- Moved verbatim from the root CLAUDE.md on 2026-10-26. Loads only when Claude touches a matching file. -->

- **Groq = 8,000 טוקנים לדקה (on_demand), ונספר prompt + max_tokens.** בקשה של 9,137 נדחתה ב-413 (2026-10-07) והניתוח נפל ל-Gemma שנכשל. `insight_ai` אורז כל הקשר לתקציב תווים (`_pack`, `CONTEXT_CHARS`, 2 תווים/טוקן שמרני; טסט מחשב שהסכום < `GROQ_TPM`), שולח `reasoning_effort="low"` (gpt-oss מבזבז max_tokens על חשיבה), ממתין `TPM_WAIT_SECONDS` בין קריאות ומנסה שוב עד `ATTEMPTS`. אחרי סנכרון חי: ניתוח AI **ואז** התקצירים (`project_sync._ai_then_briefs`) — אותו תקציב דקה.
- **httpx ברמת WARNING (`app/main.py`):** שורת ה-INFO שלו היא ה-URL המלא — טוקן הבוט בנתיב ומפתח Google ב-query נמצאו בטקסט גלוי בלוגים של Railway (2026-10-07). טסט ב-`test_key_redaction.py`. **רשת ביטחון (2026-10-20):** `_RedactKeys` ב-`app/main.py` מנקה כל רשומת לוג (הודעה + traceback) דרך `gemma_client.redact` — `llm_router` רשם `{e}` גולמי והמפתח של Google נמצא שוב בלוגים. אחרי דליפה: לסובב את המפתח ב-Google AI Studio.
- **Gemma 4 חושב לפני שהוא עונה, והחשיבה נספרת ב-`maxOutputTokens`.** בריף שמבקש 150 טוקנים קיבל `finishReason=MAX_TOKENS` בלי טקסט (82 פעמים ב-2026-10-07). `gemma_client.THINKING_HEADROOM` מתווסף מעל ה-`max_tokens` של הקורא; אורך התשובה נשלט ע"י הפרומפט. **לא** לשלוח `thinkingLevel`/`thinkingBudget` ל-Gemma — אין מתג מתועד, ושדה לא מוכר מחזיר 400 (= ספק גיבוי מת). `models=` שמגיע דרך ה-router מסונן לשמות gemma/gemini בלבד — שם מודל של Groq שנשלח לגוגל הוא 404 קבוע.
