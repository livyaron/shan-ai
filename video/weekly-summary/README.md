# סיכום שבועי — וידאו 30 שניות (Remotion)

1920×1080, 30fps, למסך חדר המבצעים. 5 סצנות: פתיחה · לפי מגזר · דרגות טיפול · מה לבדוק השבוע · סגירה.

## הרצה
```bash
npm i
npm run dev                                   # Remotion Studio
npx remotion render WeeklySummary out/w.mp4   # ברירת המחדל = נתוני דוגמה, עם חותמת
npx remotion render WeeklySummary out/w.mp4 --props=data.json   # נתונים אמיתיים
```
בסביבת ענן: `--browser-executable=/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell`.

## נתונים
החוזה היחיד: `src/data.ts` (`WeeklyData`). ברירות המחדל ב-`Root.tsx` הן **דוגמה** (`demo: true` = חותמת "נתוני דוגמה" על כל פריים).
קובץ נתונים אמיתי (`data*.json`) **לא נכנס ל-git** — קובץ המאסטר מסווג כמידע פנים והריפו ציבורי.
המספרים חייבים לבוא מהמנוע (`project_kpis` / `insight_triage` / `pattern_service`), לא מהקלדה ידנית.

הגופן Heebo מצורף מקומית (`public/fonts`, רישיון OFL) — רינדור לא תלוי ברשת.
