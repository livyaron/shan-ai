# 👥 New RACI Button & Table Interface

## 🎯 What Changed

**Before**: Had a separate "📤 הפץ" (Publish) button  
**After**: Now has a **"👥 RACI"** button that opens the RACI assignment table

---

## 📋 How to Use

### Step 1: Go to Decisions Page
- Navigate to `/dashboard/decisions`
- Find a decision card

### Step 2: Click the RACI Button
```
Card Buttons:
[🔍]  [✏️]  [🤖]  [👥]  [📊]
                    ↑
              RACI Button (new!)
```

### Step 3: See the RACI Modal
A modal opens with:
1. **RACI Rules Box** (top) — shows what R, A, C, I mean
2. **RACI Table** — users × roles matrix

---

## 🎨 RACI Table Layout

```
┌─────────────────────────────────────────────────────┐
│  משתמש            👤 R        🧠 A        💬 C        📢 I  │
│                  ביצוע      סמכות      יועץ      לידיעה  │
├─────────────────────────────────────────────────────┤
│ ירון          ○         ◉         ○         ○        │ ← Selected A
│ שמוליק        ◉         ○         ○         ○        │ ← Selected R
│ נווה          ○         ○         ◉         ○        │ ← Selected C
│ גלי           ○         ○         ○         ◉        │ ← Selected I
│ ערן           ○         ○         ◉         ○        │ ← Multiple OK for C
└─────────────────────────────────────────────────────┘
    (Click radio buttons to select)
```

---

## ✅ RACI Rules Enforced

### Rules Box Shows:
```
📌 כללי RACI:
• R (אחראי ביצוע): רק אחד
• A (בעל סמכות): רק אחד בדיוק  ← REQUIRED & UNIQUE
• C (יועץ): ניתן למספר אנשים     ← Multiple OK
• I (לידיעה): ניתן למספר אנשים    ← Multiple OK
```

### Real-Time Validation
As you click radio buttons, validation messages appear below the table:

```
✅ בעל סמכות: 1 אדם              (Good!)
✅ אחראי ביצוע: 1 אדם           (Good!)
ℹ️ יועץ: 2 אנשים                 (OK - multiple allowed)
ℹ️ לידיעה: 1 אדם                (OK - multiple allowed)
```

### Save Button Validation
When you click **💾 שמור RACI**:
- ✅ Checks that exactly ONE person is assigned as A (Accountable)
- ✅ R (Responsible) can be 0-1 or multiple
- ✅ C (Consulted) can be 0+
- ✅ I (Informed) can be 0+
- ❌ Blocks save if A is not exactly 1

---

## 🔒 RACI Role Definitions

| Role | Full Name | Hebrew | Who | Responsibilities |
|------|-----------|--------|-----|------------------|
| **R** | Responsible | אחראי ביצוע | Executor | Must execute decision, report completion |
| **A** | Accountable | בעל סמכות | Authority | Final approval, answerable for outcome |
| **C** | Consulted | יועץ | Advisor | Provides input before execution |
| **I** | Informed | לידיעה | Stakeholder | Gets update after execution |

---

## 🎯 Permissions

**Who can edit RACI?**
- ✅ The decision submitter
- ✅ Admins
- ❌ Others cannot edit

**When button appears:**
- ✅ Shows "👥" button if you can edit RACI
- ❌ Hidden if you can't edit

---

## 🔄 Auto-Approval Feature

**Special Case**: If RACI A = Submitter
- When you save RACI where Accountable is the submitter
- Decision automatically changes to **APPROVED**
- No external approval needed (they already have authority)

---

## 📤 Distribution Records

**Automatic Creation**: When RACI is saved, distribution records are created:
- **R** → `execution` (needs to execute)
- **A** → `approval` (unless they're submitter → auto-approve)
- **C** → `info` (for consultation)
- **I** → `info` (for awareness)

Users get Telegram notifications automatically! 🔔

---

## ✨ Visual Improvements

### Modal Design
- ✅ Larger modal (modal-xl instead of modal-lg)
- ✅ Color-coded rule explanations
- ✅ Better table styling with hover effects
- ✅ Real-time validation feedback
- ✅ Green/Red status indicators

### Radio Buttons
- ✅ Large clickable radio buttons (20px)
- ✅ Color-coded by role:
  - **R** = Blue (#5865f2)
  - **A** = Red (#dc3545) ← Most important!
  - **C** = Yellow (#ffc107)
  - **I** = Cyan (#17a2b8)

---

## 🧪 Testing Scenario

1. **Create a decision** or open existing
2. Click **👥** button (RACI)
3. Modal opens with rules and table
4. **Assign roles** by clicking radio buttons:
   - Select ONE person for A (Red)
   - Select ONE person for R (Blue)
   - Select multiple for C & I if needed
5. **Validation shows** real-time feedback:
   - Green ✅ if A is exactly 1
   - Red ❌ if A is 0 or >1
6. Click **💾 שמור RACI** to save
7. **Auto-redirect** to decisions page
8. **RACI saved** ✅ + distribution records created ✅

---

## 🚀 Quick Reference

| Element | What It Does |
|---------|------------|
| 👥 Button | Opens RACI modal |
| 📌 Rules Box | Explains RACI meanings |
| Table | Choose R, A, C, I for each user |
| Radio Buttons | Select roles (one per user, multiple roles OK) |
| Validation | Real-time feedback on choices |
| 💾 Save | Validates & saves RACI |
| Auto-Approve | If A = Submitter, auto-approves |
| Distribution | Creates records for notifications |

---

## ❓ FAQ

**Q: Can one person have multiple roles?**  
A: No, each person gets exactly one role per decision

**Q: Why is A (Accountable) required?**  
A: Someone must be ultimately responsible and answerable

**Q: What if A = Submitter?**  
A: Decision auto-approves (they already have authority)

**Q: Do users get notified?**  
A: Yes! Telegram notifications sent automatically when RACI is saved

**Q: Can I change RACI later?**  
A: Yes, click 👥 again to edit (submitter or admin only)

