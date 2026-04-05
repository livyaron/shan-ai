# 🔒 RACI Permission System

## Overview

**Only authorized users can edit RACI assignments.** The system enforces permissions at **3 layers**:
1. **UI Level** - Button hidden for unauthorized users
2. **Frontend Level** - Error handling for API responses
3. **Backend Level** - API permission checks

---

## Who Can Edit RACI?

| User Type | Can Edit RACI? | Reason |
|-----------|---|---------|
| **Submitter** | ✅ YES | Created the decision |
| **Admin** | ✅ YES | Has all permissions |
| **RACI A (Accountable)** | ❌ NO | Can approve, but not change assignments |
| **RACI R (Responsible)** | ❌ NO | Executes decision, can't change roles |
| **RACI C (Consulted)** | ❌ NO | Provides input, can't change roles |
| **RACI I (Informed)** | ❌ NO | Gets notified, can't change roles |
| **Other users** | ❌ NO | No involvement in decision |

---

## 3-Layer Permission Enforcement

### 1️⃣ UI Level: Button Hidden

**File**: `app/templates/decisions.html` (Line 572)

```html
{% if d.can_edit_raci %}
<button class="btn btn-outline-primary btn-sm" onclick="openRaciEdit({{ d.id }})" title="הקצאת RACI">👥</button>
{% endif %}
```

**What happens**:
- ✅ If `can_edit_raci = True` → Button shown
- ❌ If `can_edit_raci = False` → Button hidden completely

**Determined by**:
```python
can_edit_raci = current_user.is_admin or d.submitter_id == current_user.id
```

---

### 2️⃣ Frontend Level: Error Handling

**File**: `app/templates/decisions.html` (Lines 1227, 1349)

#### When Opening RACI Modal (openRaciEdit):
```javascript
if (res.status === 403) {
    document.getElementById('raciEditBody').innerHTML = 
        '<tr><td colspan="5" style="color: #dc3545;">
        <strong>⛔ אין הרשאה</strong><br/>
        רק מגיש ההחלטה או מנהל יכול לערוך RACI</td></tr>';
    return;
}
```

**What the user sees**:
```
⛔ אין הרשאה
רק מגיש ההחלטה או מנהל יכול לערוך RACI
```

#### When Saving RACI (saveRaci):
```javascript
if (res.status === 403) {
    alert('❌ אין לך הרשאה לעדכן RACI להחלטה זו\n\n' +
          'רק מגיש ההחלטה או מנהל יכול לערוך RACI');
    return;
}
```

**What the user sees**:
```
Alert Dialog:
❌ אין לך הרשאה לעדכן RACI להחלטה זו

רק מגיש ההחלטה או מנהל יכול לערוך RACI
```

---

### 3️⃣ Backend Level: API Permission Checks

**File**: `app/routers/dashboard.py`

#### GET /decisions/{decision_id}/raci (Line 1308)
```python
# Permission check: only admin or submitter can view RACI for editing
d = await session.get(Decision, decision_id)
if not d:
    return JSONResponse({"error": "החלטה לא נמצאה"}, status_code=404)
if not current_user.is_admin and d.submitter_id != current_user.id:
    return JSONResponse(
        {"error": "אין הרשאה לצפות בהקצאת RACI להחלטה זו"}, 
        status_code=403
    )
```

#### POST /decisions/{decision_id}/raci (Line 1378)
```python
# Permission check: only admin or submitter can edit RACI
if not current_user.is_admin and d.submitter_id != current_user.id:
    return JSONResponse(
        {"ok": False, "error": "אין הרשאה לעדכן RACI להחלטה זו"}, 
        status_code=403
    )
```

#### POST /decisions/{decision_id}/raci/suggest (Line 1355)
```python
# Permission check: only admin or submitter can trigger RACI suggestion
if not current_user.is_admin and d.submitter_id != current_user.id:
    return JSONResponse(
        {"error": "אין הרשאה לעדכן RACI להחלטה זו"}, 
        status_code=403
    )
```

---

## Security Scenarios

### Scenario 1: User tries to open RACI modal they don't have permission for

```
1. User sees decision card WITHOUT 👥 button (hidden by {% if %})
2. User manually calls openRaciEdit() via console (bypass)
3. Frontend calls GET /decisions/X/raci
4. Backend returns 403 Forbidden
5. Modal shows: "⛔ אין הרשאה - רק מגיש או מנהל יכול לערוך"
```

### Scenario 2: User tries to save RACI they don't have permission for

```
1. User somehow bypasses the modal (manual API call)
2. Frontend calls POST /decisions/X/raci
3. Backend returns 403 Forbidden
4. Alert shows: "❌ אין לך הרשאה לעדכן RACI..."
5. Modal closes, no changes saved
```

### Scenario 3: Admin edits RACI of other user's decision

```
1. Admin sees ALL decisions
2. 👥 button shows for all decisions (admin can edit any)
3. Admin clicks → Modal opens → Can edit RACI
4. Backend validates: is_admin = True ✅
5. RACI saved successfully
```

---

## Testing the Permission System

### Test 1: Non-Submitter Can't See Button
```
1. Login as User A
2. Find a decision submitted by User B
3. 👥 Button should NOT appear
```

### Test 2: Submitter Can See Button
```
1. Login as User A
2. Find a decision submitted by User A
3. 👥 Button SHOULD appear
```

### Test 3: API Rejects Unauthorized Changes
```
1. Login as User A (not submitter)
2. Open browser console
3. Call: fetch('/dashboard/decisions/{other_user_decision_id}/raci', {method:'GET'})
4. Response: 403 Forbidden
```

### Test 4: Admin Can Edit Any Decision
```
1. Login as Admin
2. Find ANY decision (doesn't matter who submitted it)
3. 👥 Button SHOULD appear
4. Can edit and save RACI
```

---

## Error Messages

| Situation | Error Message | HTTP Code |
|-----------|--------------|-----------|
| GET RACI without permission | "אין הרשאה לצפות בהקצאת RACI להחלטה זו" | 403 |
| POST RACI without permission | "אין הרשאה לעדכן RACI להחלטה זו" | 403 |
| Suggest RACI without permission | "אין הרשאה לעדכן RACI להחלטה זו" | 403 |
| Decision not found | "החלטה לא נמצאה" | 404 |

---

## Summary

✅ **Complete Permission System**:
- Frontend hides button for unauthorized users
- Frontend handles 403 errors gracefully
- Backend enforces permission checks on all RACI endpoints
- Users cannot bypass UI to change RACI they don't own
- Admin can edit any decision's RACI
- Error messages are clear and helpful

**Result**: Users cannot modify RACI assignments they don't have permission for, at any layer. 🔒

