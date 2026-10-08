# SO-LOCATION LOCKDOWN — Reversal Guide

**Status:** TEMPORARY
**Feature affected:** Practice (`/quiz/*`) and Competitions (`/live-quiz/*`)
**Audience:** Users with `students.location = 'SO'`
**Exempt:** Admins, impersonators, all non-SO users
**Added:** 2026-10-08
**Expected removal:** When SO-specific practice and competition is ready

---

## What this lockdown does

Blocks users whose `students.location` is `'SO'` from reaching Practice
(`/quiz/*`) or Competitions (`/live-quiz/*`).

- HTML requests → `302` redirect to `/so-unavailable` (Somali-only page)
- JSON/AJAX requests → `503` with `{error: 'so_lockdown', message: ...}`
- Admins bypass unconditionally (any admin, not just super)
- Impersonators bypass so admins can preview the SO view
- All other features (PDFs, Groups, Focus, History, Settings,
  Dashboard) work normally for SO users

---

## Files touched

| File | Change |
|---|---|
| `config.py` | +6 lines — `SO_LOCKDOWN_ENABLED` env-driven flag |
| `app.py` | +1 line in `refresh_user_state_if_needed` — caches `session['location']` |
| `app.py` | +1 block after `enforce_maintenance_mode` — the gate + route |
| `templates/so_unavailable.html` | new file — Somali-only redirect target |

**No other files were modified.**

---

## How to disable — three options

### Option 1 · Environment variable (fastest, no code change)

Add to `.env`:

```
SO_LOCKDOWN_ENABLED=false
```

Restart the web worker. The gate becomes a no-op before any check runs.

**To re-enable:** set to `true` (or remove the line — default is `true`),
restart.

### Option 2 · Revert the code (permanent removal)

Follow the reversal steps below.

### Option 3 · Emergency clear of one session

If a single SO user needs temporary access, use the admin panel to
promote them to admin for that session, or impersonate them from an
admin account.

---

## Reversal steps — precise, AI-executable

Follow in order. All edits are done by exact text match.

### Step 1 · Remove the `SO_LOCKDOWN_ENABLED` block from `config.py`

**Find this exact block** (it lives in the DEBUG / DEV MODE section,
right after `DEBUG = False`):

```python
    # ============================================
    # TEMPORARY POLICY GATES
    # ============================================
    # SO-location lockdown: when true, users whose students.location
    # is 'SO' cannot reach Practice (/quiz/*) or Competitions
    # (/live-quiz/*). Admins and impersonators bypass.
    #
    # Flip to false in .env to lift the restriction without touching
    # any code:
    #     SO_LOCKDOWN_ENABLED=false
    SO_LOCKDOWN_ENABLED = _env_bool('SO_LOCKDOWN_ENABLED', 'true')
```

**Delete the entire block, including the two comment lines that
precede and follow it.** The `DEBUG = False` line above and the
next `# ==== SECURITY ====` section must be adjacent afterward.

**Verify:** `grep -n "SO_LOCKDOWN" config.py` returns nothing.

---

### Step 2 · Remove the `session['location']` line from `app.py`

**Find this block** in `refresh_user_state_if_needed`:

```python
            session['user_state_loaded_at'] = time.time()
            session['created_at'] = student.get('created_at')
            session['location'] = (student.get('location') or '').strip().upper()
            try:
                session['first_discount_used'] = int(student.get('first_discount_used') or 0)
```

**Delete only this one line:**

```python
            session['location'] = (student.get('location') or '').strip().upper()
```

The `user_state_loaded_at`, `created_at`, and `first_discount_used`
lines must remain.

**Verify:** `grep -n "session\['location'\]" app.py` returns nothing.

---

### Step 3 · Remove the lockdown block from `app.py`

**Find the marker comment:**

```python
# ============================================================
# SO-LOCATION LOCKDOWN (temporary — see SO_LOCKDOWN.md)
# ============================================================
```

**Delete from that marker line down to and including the closing of
the `so_unavailable_page` route:**

```python
    return render_template('so_unavailable.html'), 200
```

The next non-deleted line must be:

```python
# ============================================
# CSRF PROTECTION
# ============================================
```

**Verify:** `grep -n "so_lockdown\|enforce_so_lockdown\|so_unavailable" app.py`
returns nothing.

---

### Step 4 · Delete the template

```bash
rm templates/so_unavailable.html
```

**Verify:** `ls templates/so_unavailable.html` → "No such file"

---

### Step 5 · Clean up `.env`

Remove this line if present:

```
SO_LOCKDOWN_ENABLED=false
```

(Or leave it — it has no effect once `Config` no longer defines the
attribute. `getattr(Config, 'SO_LOCKDOWN_ENABLED', False)` returns
`False` if the attribute is missing, but that path is never reached
once the gate is deleted. Either is fine.)

---

### Step 6 · Restart and verify

```bash
# find and kill the running process
pkill -f "python app.py"

# start it again
python app.py
```

**Verification matrix:**

| Check | Expected |
|---|---|
| `python -c "from config import Config; print(hasattr(Config, 'SO_LOCKDOWN_ENABLED'))"` | `False` |
| `grep -rn "so_lockdown\|enforce_so_lockdown\|SO_LOCKDOWN" *.py templates/ 2>/dev/null` | empty output |
| `ls templates/so_unavailable.html 2>&1` | "No such file or directory" |
| Log in as an SO user, visit `/quiz/` | Normal practice setup page loads |
| Log in as an SO user, visit `/live-quiz/lobby` | Normal lobby loads |
| Check `logs/app.log` | No new `so.lockdown.blocked` lines |

Once all six pass, the lockdown is fully removed.

---

## What to expect if you re-apply this

To reinstate the lockdown, restore the four pieces above in reverse:

1. Add the `SO_LOCKDOWN_ENABLED` block to `config.py`
2. Add the `session['location']` line to `refresh_user_state_if_needed`
3. Paste the lockdown block from Part 1 of this doc into `app.py`
4. Paste `templates/so_unavailable.html`

The block sits after `enforce_maintenance_mode` and before the CSRF
section. Order matters — the maintenance gate must run first so that
platform-wide maintenance still wins over the SO gate during a
maintenance window.

---

## Edge cases and design notes

### Fail-open on DB error

`_get_session_location()` returns `''` on any DB error. An empty
string is not `'SO'`, so the user is **not** gated. This is
intentional — a database hiccup should never lock a user out of the
platform.

### Fail-open on missing session location

If a session was created before this change shipped, `session['location']`
will be missing. The lazy fetch reads it from the DB on first access
and caches the result. No user is left in limbo.

### Stale location after a profile edit

If a user changes their location from `'SO'` to `'PL'`, the change
takes effect on their next hourly session reload. Worst case: one
hour of unnecessary blocking. If this becomes a problem, replace
`_get_session_location()` with a fresh DB read on every gated
request — it's one row read per hit.

### Impersonation

Admin impersonating an SO user: `session['is_impersonating']` is
`True`, and the lockdown bypasses so the admin can inspect the SO
experience. If you want impersonators to see the gate instead, remove
the `is_impersonating` check in the enforcement function.

### Logging

Every blocked request writes one line to `logs/app.log`:

```
so.lockdown.blocked user=42 loc=SO path=/quiz/play
```

One line per blocked request, no counters, no DB writes. To count
blocks:

```bash
grep -c "so.lockdown.blocked" logs/app.log
```

To count unique blocked users in the last 1000 lines:

```bash
grep "so.lockdown.blocked" logs/app.log | tail -1000 \
  | sed -E 's/.*user=([0-9]+).*/\1/' | sort -u | wc -l
```

### JSON vs HTML response

The gate distinguishes by three signals, any of which triggers the
JSON path:

- Path starts with `/api/`
- Header `X-Requested-With: XMLHttpRequest`
- `Accept` header prefers `application/json`

This matches the same test used by `enforce_maintenance_mode`, so
the two gates behave identically for API clients.

### Route ordering

`enforce_so_lockdown` runs **after** `enforce_maintenance_mode`. This
means:

- If the platform is in maintenance, everyone sees the maintenance
  page first, including SO users.
- If maintenance is off, SO users hitting gated routes get the SO
  lockdown page.

If the order were reversed, SO users would see the lockdown page
even when the whole platform was down for maintenance. That's why
the order matters.

---

## Cross-references

- The gate itself: `app.py`, search for `SO-LOCATION LOCKDOWN`
- The page: `templates/so_unavailable.html`
- The flag: `config.py`, search for `SO_LOCKDOWN_ENABLED`
- The session cache: `app.py`, search for `session['location']`
- Related: `enforce_maintenance_mode` (same pattern, same
  before_request chain)

---

## Revision history

| Date | Change |
|---|---|
| 2026-10-08 | Initial lockdown applied (SO users gated from Practice + Competitions) |
| — | (add entries here when the lockdown is lifted or modified) |