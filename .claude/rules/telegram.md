---
paths:
  - "app/main.py"
  - "app/services/telegram_*.py"
  - "app/routers/telegram.py"
  - "tests/test_telegram_*.py"
---
# Telegram bot (webhook on Railway)

<!-- Moved verbatim from the root CLAUDE.md on 2026-10-26. Loads only when Claude touches a matching file. -->

- **הבוט על Railway עובד ב-WEBHOOK, לא polling** (`app/main.py` startup: `RAILWAY_PUBLIC_DOMAIN` קיים ⇒ `set_webhook`; polling הוא רק מסלול הפיתוח המקומי). ה-registration הוא **גלובלי לטוקן**, לא לקונטיינר — ולכן:
    1. **אסור ל-`deleteWebhook` ב-shutdown.** בדיפלוי הקונטיינר החדש רושם webhook בזמן שהישן נכבה; `deleteWebhook` בדרך החוצה מוחק את הרישום שהחדש בדיוק יצר, האתר נשאר חי, הדיפלוי ירוק — והבוט אילם עד ריסטרט ידני. זה הבאג שאובחן ב-2026-09-21.
    2. **כל instance מקומי שמריץ polling מוחק את ה-webhook של הפרודקשן** — PTB קורא `delete_webhook` בתוך `start_polling` תמיד (`telegram/ext/_updater.py`), לא רק "גונב" את ה-polling.
    3. `_webhook_watchdog` ב-`app/main.py` (כל `WEBHOOK_WATCHDOG_SECONDS`=300) שואל את טלגרם אם הרישום עוד מצביע עלינו ומתקן אותו; תיקון רץ עם `drop_pending_updates=False` (הודעות שנצברו בפער הן שאלות אמיתיות), האתחול רץ עם `True`.
    4. **אבחון מבחוץ:** `GET /telegram/webhook_status` (מה טלגרם חושב: `healthy`, `registered_url`, `pending_update_count`, `last_error_message`) ו-`GET /telegram/webhook_repair` (רישום מחדש מיד, ללא המתנה ל-watchdog). זו התשובה היחידה לשאלה "למה הבוט לא עונה" כשהאתר עצמו עובד — `/health` לא יודע כלום על טלגרם.
