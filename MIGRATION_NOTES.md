# MIGRATION NOTES — NuunPlatform

A running log of the four-system migration. Read in order, top to bottom.
Latest phase at the bottom.

---

## Phase 1a — Tier vocabulary (schema + config)

**Status:** ✅ Complete (previous sprint)

Renamed the tier system from `danbe / dhexe / hore` to `free / premium / pro`
throughout the schema, config, and business logic.

- `schema.sql` — CHECK constraints now enforce `('free', 'premium', 'pro')`
  on: `students.tier`, `groups.tier_required`, `achievements.tier_required`,
  `discount_codes.applies_to`, `upgrade_requests.requested_tier`.
- `tier_config.py` — marked DEPRECATED. `normalize_tier()` maps any legacy
  value to its canonical form. `get_tier_level()` returns the ordinal
  (free=0, premium=1, pro=2). These two helpers are the ONLY things allowed
  to know about the old vocabulary until Phase 6 deletes this file.

---

## Phase 1b — Data migration

**Status:** ✅ Complete (previous sprint)

- `bbbb.py` — one-shot script that renamed every legacy value in every
  column listed above. Safe (snapshot, dry-run, idempotent).
- **TODO (deferred to Phase 6):** Rename this file to something meaningful
  (e.g. `scripts/migrations/0001_normalize_tiers.py`) and delete the old name.

---

## Phase 1c — Entitlement system (tables + seed + bootstrap)

**Status:** ✅ Complete (previous sprint)

- `schema.sql` — three new tables:
  - `entitlement_features` — one row per feature
  - `entitlement_policies` — one row per feature × tier
  - `entitlement_audit`   — admin change log
- `entitlements_seed.json` — initial 40+ feature definitions with per-tier
  policies. Applied once on a fresh install.
- `startup.py::bootstrap_entitlements()` — reads the seed file and inserts
  the features + policies. Uses `PRAGMA user_version = 1` to mark completion.
- `services/entitlement_service.py` — full implementation:
  - Policy cache (in-process, reloadable)
  - Tier resolution (session → DB fallback, expiry-aware)
  - `check()`, `get_level()`, `get_limit()`, `get_remaining()`, `consume()`
  - Admin CRUD with audit log
  - JSON export/import
  - `refresh_user(user_id)` — writes `instance/user_state_changes.flag`

---

## Phase 2 — Entitlement integration

**Status:** ✅ Complete (this sprint)

Goal: make the entitlement system authoritative. Every tier decision in the
application now flows through `services/entitlement_service.py`. The old
`tier_config.py` remains only as a vocabulary bridge — Phase 6 deletes it.

### Files changed

| # | File | Nature |
|---|---|---|
| 1 | `services/tier_service.py` | **Rewritten.** Now a thin facade over `entitlement_service`. Same public API — no caller changes needed. `_QUOTA_KEY_ALIASES` bridges legacy keys (`quiz_attempt`) to canonical (`quiz_attempts`). |
| 2 | `migrate_user_usage_keys.py` | **NEW.** One-shot data migration: `user_usage.metric_code` renamed to match entitlement feature keys. Must run BEFORE deploying the new `tier_service.py`. |
| 3 | `admin_users_db.py` | Tier vocabulary normalized. `SORT_MAP['tier_high']`, `_build_user_filter_sql`, `get_users_admin_stats`, `set_user_tier_admin`, `bulk_user_action('set_tier')` all updated. `set_user_tier_admin` now calls `entitlement_service.refresh_user()`. |
| 4 | `platform_activity.py` | `get_tier_distribution()` returns canonical keys (`free`/`premium`/`pro`) — the admin dashboard tier bar was returning zeros. |
| 5 | `services/group_service.py` | Pro cross-curriculum bypass was dead code (`if user_tier == 'hore'` never fired). Now `if user_tier == 'pro'`. |
| 6 | `db.py` | Two lines: added `from tier_config import normalize_tier`; `create_group_advanced` default changed from `'danbe'` to `normalize_tier(...) or 'free'`. Also normalized `tier_required` on update. |
| 7 | `blueprints/live_quiz_bp.py` | User-facing flash messages: "Safka Dhexe or Safka Hore" → "Premium or Pro". |
| 8 | `services/achievement_service.py` | Achievement definitions now use `free`/`premium` (schema CHECK would reject `danbe`/`dhexe` on fresh install). |
| 9 | `blueprints/quiz_bp.py` | Leaderboard JSON path fix — `$.privacy.show_public_id` → `$."privacy.show_public_id"`. Same fix already applied in `db.py::get_leaderboard`. |

### Known issues deferred to later phases

- **Premium quiz-questions limit:** The entitlement seed sets premium = **20**.
  The old `tier_config.get_allowed_question_counts` gave premium the **30**
  option. As of Phase 2, premium users will lose the 30-question option.
  If UX feedback says 30 was expected, bump the seed value in
  `entitlements_seed.json` before first bootstrap on a fresh install.
  Existing installs must be updated via the admin policy API (or a SQL
  `UPDATE entitlement_policies ...`).

- **`services/settings_bp.py::api_password`** — uses plaintext comparison
  and writes plaintext. This is a pre-existing security bug and is
  **outside the scope of Phase 2.** Tracked as a Phase 5 hardening item.
  The correct implementation exists in `blueprints/user_settings_bp.py`
  but that blueprint is not registered. Do NOT delete either file until
  the swap is done deliberately.

- **`tier_config.py`** still imported by:
  - `services/tier_service.py` (for `normalize_tier` / `get_tier_level`)
  - `db.py` (for `normalize_tier`)
  - `admin_users_db.py`, `platform_activity.py`, `services/group_service.py`
  These are the ONLY remaining consumers. Phase 6 will inline
  `normalize_tier` into a single helper and delete the file.

### Deploy order (Phase 2)

```bash
# 1. Backup the database
cp nuunplatform.db nuunplatform.db.pre_phase2

# 2. Run the data migration
python migrate_user_usage_keys.py --dry-run   # inspect
python migrate_user_usage_keys.py             # apply

# 3. Overwrite all 9 files from this phase

# 4. Restart the app