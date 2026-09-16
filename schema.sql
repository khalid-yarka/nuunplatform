-- ============================================
-- NUUNPLATFORM DATABASE SCHEMA
-- ============================================

-- ============================================
-- STUDENTS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT UNIQUE NOT NULL,
    phone_number TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    first_name TEXT NOT NULL,
    middle_name TEXT DEFAULT '',
    last_name TEXT NOT NULL,
    location TEXT DEFAULT '',
    city TEXT DEFAULT '',
    school TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    total_points INTEGER DEFAULT 0,
    is_admin INTEGER DEFAULT 0,
    is_verified INTEGER NOT NULL DEFAULT 0,
    curriculum TEXT,
    tier TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'premium', 'pro')),
    tier_expires_at TEXT,
    tier_updated_at TEXT,
    last_login_at TEXT,
    last_login_ip TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_students_phone ON students(phone_number);
CREATE INDEX IF NOT EXISTS idx_students_public_id ON students(public_id);
CREATE INDEX IF NOT EXISTS idx_students_tier ON students(tier);
CREATE INDEX IF NOT EXISTS idx_students_verified ON students(is_verified);

-- ============================================
-- QUESTIONS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_code TEXT NOT NULL,
    question_text TEXT NOT NULL,
    options TEXT NOT NULL,
    correct_answer TEXT NOT NULL CHECK (correct_answer IN ('A', 'B', 'C', 'D', 'E', 'F')),
    difficulty INTEGER DEFAULT 1,
    chapter TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    explanation TEXT DEFAULT '',
    pdf_code TEXT DEFAULT NULL,
    pdf_page INTEGER DEFAULT NULL,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'archived', 'draft')),
    version INTEGER DEFAULT 1,
    created_by INTEGER,
    updated_by INTEGER,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_questions_subject ON questions(subject_code);
CREATE INDEX IF NOT EXISTS idx_questions_status ON questions(status);
CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty);
CREATE INDEX IF NOT EXISTS idx_questions_created_by ON questions(created_by);
CREATE INDEX IF NOT EXISTS idx_questions_created_at ON questions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_questions_updated_at ON questions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_questions_subject_status ON questions(subject_code, status);

-- ============================================
-- QUIZ ATTEMPTS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    subject_code TEXT NOT NULL,
    score INTEGER DEFAULT 0,
    total_questions INTEGER DEFAULT 0,
    answers TEXT,
    ratings TEXT,
    reactions TEXT,
    completed_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_attempts_student ON quiz_attempts(student_id);
CREATE INDEX IF NOT EXISTS idx_attempts_subject ON quiz_attempts(subject_code);
CREATE INDEX IF NOT EXISTS idx_attempts_completed ON quiz_attempts(completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_student_completed ON quiz_attempts(student_id, completed_at DESC);

-- ============================================
-- GROUPS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('whatsapp', 'telegram')),
    invite_link TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT '',
    curriculum TEXT DEFAULT '',
    subjects TEXT DEFAULT '',
    tier_required TEXT NOT NULL DEFAULT 'free'
        CHECK (tier_required IN ('free', 'premium', 'pro')),
    group_type TEXT DEFAULT 'community',
    icon TEXT DEFAULT '📚',
    display_order INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    is_featured INTEGER DEFAULT 0,
    click_count INTEGER DEFAULT 0,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_groups_platform ON groups(platform);
CREATE INDEX IF NOT EXISTS idx_groups_category ON groups(category);
CREATE INDEX IF NOT EXISTS idx_groups_active ON groups(is_active);
CREATE INDEX IF NOT EXISTS idx_groups_featured ON groups(is_featured);
CREATE INDEX IF NOT EXISTS idx_groups_click_count ON groups(click_count DESC);

-- ============================================
-- MAIN PDFs — Published library
-- Location: nuunplatform.db (main DB)
--
-- The bot's staging `pdfs` table (with file_id / file_unique_id /
-- original_filename) lives in bot_data.db and is created by
-- bot/db.py::init_bot_db(). Do NOT add the bot schema here.
--
-- The `file_id` and `file_unique_id` columns below are COPIED from
-- the bot staging row at publish time, so main is self-sufficient
-- and the staging row can remain as a permanent backup.
-- ============================================

CREATE TABLE IF NOT EXISTS pdfs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT UNIQUE NOT NULL,
    title          TEXT NOT NULL,
    description    TEXT DEFAULT '',
    curriculum     TEXT DEFAULT 'PL' CHECK (curriculum IN ('PL', 'SO', 'SL')),
    class          TEXT DEFAULT ''   CHECK (class IN ('', '7aad', '8aad', 'F3', 'F4')),
    subject        TEXT NOT NULL,
    chapter        TEXT DEFAULT '',
    tags           TEXT DEFAULT '',
    is_premium     INTEGER DEFAULT 0,
    file_url       TEXT,
    file_id        TEXT,
    file_unique_id TEXT UNIQUE,
    uploaded_by    TEXT NOT NULL DEFAULT 'NUUN',
    uploaded_at    TEXT DEFAULT (datetime('now', 'localtime')),
    view_count     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pdfs_code           ON pdfs(code);
CREATE INDEX IF NOT EXISTS idx_pdfs_curriculum     ON pdfs(curriculum);
CREATE INDEX IF NOT EXISTS idx_pdfs_class          ON pdfs(class);
CREATE INDEX IF NOT EXISTS idx_pdfs_subject        ON pdfs(subject);
CREATE INDEX IF NOT EXISTS idx_pdfs_view_count     ON pdfs(view_count DESC);
CREATE INDEX IF NOT EXISTS idx_pdfs_uploaded_at    ON pdfs(uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_pdfs_file_unique_id ON pdfs(file_unique_id);

-- ============================================
-- UNVERIFIED PDFs (direct-publish review queue)
-- ============================================

CREATE TABLE IF NOT EXISTS unverified_pdfs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_id        INTEGER NOT NULL UNIQUE,
    published_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    confirmed     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (pdf_id) REFERENCES pdfs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_unverified_pdfs_confirmed
    ON unverified_pdfs(confirmed);
CREATE INDEX IF NOT EXISTS idx_unverified_pdfs_published
    ON unverified_pdfs(published_at DESC);

-- ============================================
-- LIVE QUIZZES TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS live_quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id INTEGER NOT NULL,
    title TEXT DEFAULT '',
    subject_code TEXT NOT NULL,
    question_count INTEGER DEFAULT 10,
    join_code TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'waiting' CHECK (status IN ('waiting', 'scheduled', 'active', 'finished')),
    max_participants INTEGER DEFAULT 50,
    time_per_question INTEGER DEFAULT 30,
    current_question_index INTEGER DEFAULT 0,
    question_ids TEXT,
    started_at TEXT,
    ended_at TEXT,
    scheduled_start TEXT,
    is_public INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (creator_id) REFERENCES students(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_live_quizzes_code ON live_quizzes(join_code);
CREATE INDEX IF NOT EXISTS idx_live_quizzes_status ON live_quizzes(status);
CREATE INDEX IF NOT EXISTS idx_live_quizzes_creator ON live_quizzes(creator_id);
CREATE INDEX IF NOT EXISTS idx_live_quizzes_subject ON live_quizzes(subject_code);
CREATE INDEX IF NOT EXISTS idx_live_quizzes_created ON live_quizzes(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_live_quizzes_scheduled ON live_quizzes(scheduled_start);
CREATE INDEX IF NOT EXISTS idx_live_quizzes_status_created ON live_quizzes(status, created_at DESC);

-- ============================================
-- LIVE QUIZ PARTICIPANTS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS live_quiz_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    score INTEGER DEFAULT 0,
    current_question_index INTEGER DEFAULT 0,
    correct_count INTEGER DEFAULT 0,
    wrong_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    answers TEXT,
    ratings TEXT,
    ranking INTEGER,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'left')),
    is_ready INTEGER DEFAULT 0,
    joined_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (quiz_id) REFERENCES live_quizzes(id) ON DELETE CASCADE,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    UNIQUE(quiz_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_participants_quiz ON live_quiz_participants(quiz_id);
CREATE INDEX IF NOT EXISTS idx_participants_student ON live_quiz_participants(student_id);
CREATE INDEX IF NOT EXISTS idx_participants_score ON live_quiz_participants(score DESC);
CREATE INDEX IF NOT EXISTS idx_participants_ranking ON live_quiz_participants(ranking);
CREATE INDEX IF NOT EXISTS idx_live_quiz_participants_quiz_score ON live_quiz_participants(quiz_id, score DESC);

-- ============================================
-- DELETED USERS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS deleted_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id INTEGER,
    public_id TEXT,
    first_name TEXT,
    last_name TEXT,
    phone_number TEXT,
    school TEXT,
    grade TEXT,
    total_points INTEGER,
    is_admin INTEGER,
    location TEXT,
    city TEXT,
    deleted_by INTEGER,
    data TEXT NOT NULL,
    deleted_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (deleted_by) REFERENCES students(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_deleted_public_id ON deleted_users(public_id);
CREATE INDEX IF NOT EXISTS idx_deleted_at ON deleted_users(deleted_at DESC);
CREATE INDEX IF NOT EXISTS idx_deleted_deleted_by ON deleted_users(deleted_by);

-- ============================================
-- QUIZ RATINGS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS quiz_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    rating TEXT NOT NULL CHECK (rating IN ('HAA', 'MAY')),
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ratings_student ON quiz_ratings(student_id);
CREATE INDEX IF NOT EXISTS idx_ratings_question ON quiz_ratings(question_id);
CREATE INDEX IF NOT EXISTS idx_ratings_rating ON quiz_ratings(rating);
CREATE INDEX IF NOT EXISTS idx_ratings_created ON quiz_ratings(created_at DESC);

-- ============================================
-- NOTIFICATIONS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    link TEXT DEFAULT '',
    icon TEXT DEFAULT '',
    is_read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    read_at TEXT DEFAULT NULL,
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_type ON notifications(type);
CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications(user_id, is_read);

-- ============================================
-- NOTIFICATION PREFERENCES TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS notification_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    notification_type TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE,
    UNIQUE(user_id, notification_type)
);

CREATE INDEX IF NOT EXISTS idx_pref_user ON notification_preferences(user_id);
CREATE INDEX IF NOT EXISTS idx_pref_type ON notification_preferences(notification_type);

-- ============================================
-- ACTIVITY LOGS
-- ============================================

CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    session_id TEXT,
    activity_type TEXT NOT NULL,
    severity TEXT DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'critical')),
    message TEXT NOT NULL,
    metadata TEXT,
    ip_address TEXT,
    user_agent TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_type ON activity_logs(activity_type);

-- ============================================
-- BACKUP CONFIGURATION
-- ============================================

CREATE TABLE IF NOT EXISTS backup_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    daily_retention INTEGER DEFAULT 7,
    weekly_retention INTEGER DEFAULT 4,
    monthly_retention INTEGER DEFAULT 12,
    scheduled_enabled INTEGER DEFAULT 0,
    scheduled_type TEXT DEFAULT 'daily',
    scheduled_time TEXT DEFAULT '02:00',
    last_modified TEXT DEFAULT (datetime('now', 'localtime'))
);

INSERT OR IGNORE INTO backup_config (id) VALUES (1);

-- ============================================
-- BACKUP OPERATIONS LOG
-- ============================================

CREATE TABLE IF NOT EXISTS backup_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,
    backup_filename TEXT,
    triggered_by INTEGER,
    status TEXT CHECK (status IN ('started', 'success', 'failed')),
    message TEXT,
    started_at TEXT DEFAULT (datetime('now', 'localtime')),
    completed_at TEXT,
    FOREIGN KEY (triggered_by) REFERENCES students(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_backup_ops_started ON backup_operations(started_at DESC);

-- ============================================
-- USER SETTINGS
-- ============================================

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY REFERENCES students(id) ON DELETE CASCADE,
    settings TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- ============================================
-- TIER SYSTEM TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS user_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    metric_code TEXT NOT NULL,
    period_start TEXT NOT NULL,
    usage_count INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE,
    UNIQUE(user_id, metric_code, period_start)
);
CREATE INDEX IF NOT EXISTS idx_user_usage_user_period
    ON user_usage(user_id, metric_code, period_start);

CREATE TABLE IF NOT EXISTS saved_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    content_id INTEGER NOT NULL,
    saved_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE,
    UNIQUE(user_id, content_type, content_id)
);
CREATE INDEX IF NOT EXISTS idx_saved_content_user ON saved_content(user_id);

CREATE TABLE IF NOT EXISTS achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    icon TEXT,
    tier_required TEXT DEFAULT 'free' CHECK (tier_required IN ('free', 'premium', 'pro')),
    unlock_condition TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS user_achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    achievement_id INTEGER NOT NULL,
    unlocked_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (achievement_id) REFERENCES achievements(id) ON DELETE CASCADE,
    UNIQUE(user_id, achievement_id)
);

-- ============================================
-- QUESTION INTERACTIONS
-- ============================================

CREATE TABLE IF NOT EXISTS question_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    interaction_type TEXT NOT NULL CHECK (interaction_type IN ('like', 'save', 'report')),
    report_reason TEXT,
    report_comment TEXT,
    report_status TEXT DEFAULT 'pending' CHECK (report_status IN ('pending', 'resolved', 'dismissed')),
    admin_reply TEXT,
    resolved_by INTEGER,
    resolved_at TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    UNIQUE(user_id, question_id, interaction_type)
);

CREATE INDEX IF NOT EXISTS idx_question_interactions_user ON question_interactions(user_id);
CREATE INDEX IF NOT EXISTS idx_question_interactions_question ON question_interactions(question_id);
CREATE INDEX IF NOT EXISTS idx_question_interactions_type ON question_interactions(interaction_type);
CREATE INDEX IF NOT EXISTS idx_question_interactions_report_status ON question_interactions(report_status);

-- ============================================
-- ERROR LOGS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS error_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('CRITICAL', 'ERROR', 'WARNING')),
    status_code INTEGER,
    url TEXT,
    method TEXT,
    user_id INTEGER,
    ip_address TEXT,
    error_type TEXT,
    error_message TEXT,
    stack_trace TEXT,
    user_description TEXT,
    occurrence_count INTEGER DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT,
    resolved INTEGER DEFAULT 0,
    dismissed INTEGER DEFAULT 0,
    resolution_note TEXT,
    error_hash TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_error_logs_timestamp ON error_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_error_logs_severity ON error_logs(severity);
CREATE INDEX IF NOT EXISTS idx_error_logs_resolved ON error_logs(resolved);
CREATE INDEX IF NOT EXISTS idx_error_logs_error_hash ON error_logs(error_hash);
CREATE INDEX IF NOT EXISTS idx_error_logs_request_id ON error_logs(request_id);

-- ============================================
-- LIVE QUIZ EVENTS
-- ============================================

CREATE TABLE IF NOT EXISTS live_quiz_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER NOT NULL,
    user_id INTEGER,
    event_type TEXT NOT NULL,
    question_id INTEGER,
    payload TEXT,
    sequence INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_live_quiz_events_quiz_sequence
    ON live_quiz_events(quiz_id, sequence);

-- ============================================
-- LIVE QUIZ CHECKPOINTS
-- ============================================

CREATE TABLE IF NOT EXISTS live_quiz_checkpoints (
    quiz_id INTEGER PRIMARY KEY,
    checkpoint_data TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- ============================================
-- HISTORY ENTRIES
-- ============================================

CREATE TABLE IF NOT EXISTS history_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    entry_type TEXT NOT NULL CHECK (entry_type IN (
        'quiz_attempt', 'live_quiz', 'pdf_view', 'pdf_download',
        'save', 'achievement', 'like', 'report'
    )),
    action TEXT NOT NULL CHECK (action IN (
        'completed', 'joined', 'viewed', 'downloaded',
        'saved', 'unsaved', 'unlocked', 'liked', 'unliked', 'reported'
    )),
    entry_id INTEGER,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_history_user_created
    ON history_entries(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_type ON history_entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_history_user_type
    ON history_entries(user_id, entry_type);
CREATE INDEX IF NOT EXISTS idx_history_created ON history_entries(created_at DESC);

-- ============================================
-- DISCOUNT CODES
-- ============================================

CREATE TABLE IF NOT EXISTS discount_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    discount_type TEXT NOT NULL CHECK (discount_type IN ('percentage', 'fixed')),
    discount_value INTEGER NOT NULL,
    applies_to TEXT NOT NULL CHECK (applies_to IN ('all', 'premium', 'pro')),
    max_uses INTEGER,
    used_count INTEGER DEFAULT 0,
    expires_at TEXT,
    is_active INTEGER DEFAULT 1,
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT,
    FOREIGN KEY (created_by) REFERENCES students(id) ON DELETE SET NULL
);

-- ============================================
-- UPGRADE REQUESTS
-- ============================================

CREATE TABLE IF NOT EXISTS upgrade_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    requested_tier TEXT NOT NULL CHECK (requested_tier IN ('premium', 'pro')),
    duration TEXT NOT NULL CHECK (duration IN ('monthly', 'term', 'yearly')),
    original_price_cents INTEGER NOT NULL,
    discount_code_id INTEGER,
    discount_amount_cents INTEGER DEFAULT 0,
    final_price_cents INTEGER NOT NULL,
    user_note TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    admin_id INTEGER,
    admin_note TEXT,
    expiry_date TEXT,
    approved_at TEXT,
    rejected_at TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT,
    FOREIGN KEY (user_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (discount_code_id) REFERENCES discount_codes(id) ON DELETE SET NULL,
    FOREIGN KEY (admin_id) REFERENCES students(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_upgrade_requests_user ON upgrade_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_upgrade_requests_status ON upgrade_requests(status);
CREATE INDEX IF NOT EXISTS idx_upgrade_requests_created ON upgrade_requests(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_upgrade_requests_request_id ON upgrade_requests(request_id);
CREATE INDEX IF NOT EXISTS idx_discount_codes_code ON discount_codes(code);
CREATE INDEX IF NOT EXISTS idx_discount_codes_expires ON discount_codes(expires_at);

-- ============================================
-- ADMIN USER ACTIONS
-- ============================================

CREATE TABLE IF NOT EXISTS admin_user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    target_user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_admin_user_actions_target
    ON admin_user_actions(target_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_user_actions_admin
    ON admin_user_actions(admin_id, created_at DESC);

-- ============================================
-- GROUP AUDIT LOG
-- ============================================

CREATE TABLE IF NOT EXISTS group_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    admin_id INTEGER,
    action TEXT NOT NULL,
    changes TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_group_audit_log_group
    ON group_audit_log(group_id, created_at DESC);

-- ============================================
-- ENTITLEMENT SYSTEM (Phase 1c)
-- ============================================

CREATE TABLE IF NOT EXISTS entitlement_features (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_key       TEXT NOT NULL UNIQUE,
    display_name      TEXT NOT NULL,
    description       TEXT DEFAULT '',
    category          TEXT NOT NULL,
    policy_type       TEXT NOT NULL CHECK (policy_type IN (
                          'permission','level','quota','content'
                      )),
    unit_hint         TEXT,
    is_global_active  INTEGER NOT NULL DEFAULT 1,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    notes             TEXT DEFAULT '',
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    updated_at        TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_entitlement_features_category
    ON entitlement_features(category, sort_order);

CREATE TABLE IF NOT EXISTS entitlement_policies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id   INTEGER NOT NULL,
    tier         TEXT NOT NULL CHECK (tier IN ('free','premium','pro')),
    is_enabled   INTEGER NOT NULL DEFAULT 1,
    level_value  INTEGER,
    limit_value  INTEGER,
    limit_unit   TEXT,
    updated_at   TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (feature_id) REFERENCES entitlement_features(id)
        ON DELETE CASCADE,
    UNIQUE(feature_id, tier)
);

CREATE INDEX IF NOT EXISTS idx_entitlement_policies_feature
    ON entitlement_policies(feature_id);

CREATE TABLE IF NOT EXISTS entitlement_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id     INTEGER NOT NULL,
    action       TEXT NOT NULL CHECK (action IN (
                     'feature_create','feature_update','feature_delete',
                     'policy_create','policy_update','policy_delete',
                     'bulk_update','import','export','active_toggle'
                 )),
    feature_id   INTEGER,
    feature_key  TEXT,
    tier         TEXT,
    old_value    TEXT,
    new_value    TEXT,
    reason       TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (admin_id) REFERENCES students(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_entitlement_audit_feature
    ON entitlement_audit(feature_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_entitlement_audit_admin
    ON entitlement_audit(admin_id, created_at DESC);

-- ============================================
-- ENTITLEMENT OVERRIDE SYSTEM (Phase A)
-- ============================================
-- The entitlement registry (entitlements_seed.json)
-- defines default policies per feature per tier.
-- These tables store only what the super admin
-- has explicitly changed (registry + override pattern).
-- ============================================

CREATE TABLE IF NOT EXISTS entitlement_overrides (
    feature_key  TEXT     NOT NULL,
    tier         TEXT     NOT NULL CHECK (tier IN ('free', 'premium', 'pro')),
    field        TEXT     NOT NULL CHECK (field IN (
                              'is_enabled', 'level_value',
                              'limit_value', 'limit_unit'
                          )),
    value        TEXT,
    updated_by   INTEGER,
    updated_at   TEXT     DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (feature_key, tier, field),
    FOREIGN KEY (updated_by) REFERENCES students(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_entitlement_overrides_feature
    ON entitlement_overrides(feature_key);

CREATE TABLE IF NOT EXISTS entitlement_version (
    id       INTEGER  PRIMARY KEY CHECK (id = 1),
    version  INTEGER  NOT NULL DEFAULT 1
);

INSERT OR IGNORE INTO entitlement_version (id, version) VALUES (1, 1);

-- ============================================
-- ADMIN CAPABILITY SYSTEM (Phase A)
-- ============================================
-- The capability registry lives in Python code
-- (services/admin/registry.py). These tables hold
-- only the runtime state: what is enabled, what
-- overrides exist, and the audit trail.
-- ============================================

CREATE TABLE IF NOT EXISTS admin_capability_grants (
    capability_key  TEXT     PRIMARY KEY,
    is_enabled      INTEGER  NOT NULL DEFAULT 0,
    updated_by      INTEGER,
    updated_at      TEXT     DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (updated_by) REFERENCES students(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS admin_capability_overrides (
    admin_id        INTEGER  NOT NULL,
    capability_key  TEXT     NOT NULL,
    is_enabled      INTEGER  NOT NULL,
    granted_by      INTEGER,
    granted_at      TEXT     DEFAULT (datetime('now', 'localtime')),
    note            TEXT     DEFAULT '',
    PRIMARY KEY (admin_id, capability_key),
    FOREIGN KEY (admin_id)   REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (granted_by) REFERENCES students(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_capability_overrides_admin
    ON admin_capability_overrides(admin_id);
CREATE INDEX IF NOT EXISTS idx_admin_capability_overrides_key
    ON admin_capability_overrides(capability_key);

CREATE TABLE IF NOT EXISTS admin_capability_version (
    id       INTEGER  PRIMARY KEY CHECK (id = 1),
    version  INTEGER  NOT NULL DEFAULT 1
);

INSERT OR IGNORE INTO admin_capability_version (id, version) VALUES (1, 1);

CREATE TABLE IF NOT EXISTS admin_capability_audit (
    id              INTEGER  PRIMARY KEY AUTOINCREMENT,
    scope           TEXT     NOT NULL CHECK (scope IN ('global', 'per_admin')),
    admin_id        INTEGER,
    capability_key  TEXT     NOT NULL,
    old_value       INTEGER,
    new_value       INTEGER,
    actor_id        INTEGER,
    actor_phone     TEXT,
    note            TEXT     DEFAULT '',
    created_at      TEXT     DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (actor_id) REFERENCES students(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_capability_audit_admin
    ON admin_capability_audit(admin_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_capability_audit_key
    ON admin_capability_audit(capability_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_capability_audit_actor
    ON admin_capability_audit(actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_capability_audit_created
    ON admin_capability_audit(created_at DESC);

-- ============================================
-- UNIFIED ADMIN AUDIT LOG (Phase A)
-- ============================================
-- Every write action by any admin goes here.
-- Written by the @admin_action decorator
-- (services/admin/audit.py::write_audit).
-- ============================================

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    actor_id      INTEGER,
    actor_role    TEXT     CHECK (actor_role IN ('super', 'admin', 'user')),
    actor_phone   TEXT,
    action        TEXT     NOT NULL,
    target_type   TEXT,
    target_id     INTEGER,
    before_value  TEXT,
    after_value   TEXT,
    note          TEXT     DEFAULT '',
    ip_address    TEXT,
    user_agent    TEXT,
    severity      TEXT     NOT NULL DEFAULT 'info'
                          CHECK (severity IN ('info', 'warning', 'critical')),
    created_at    TEXT     DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (actor_id) REFERENCES students(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_log_actor
    ON admin_audit_log(actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_target
    ON admin_audit_log(target_type, target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_action
    ON admin_audit_log(action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_severity
    ON admin_audit_log(severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created
    ON admin_audit_log(created_at DESC);



-- ============================================
-- UNVERIFIED PDFs (direct-publish review queue)
-- ============================================
-- Records PDFs published directly from intake via the super-admin
-- "Super Publish" action. Metadata was auto-generated from the
-- filename and needs review by any admin.
-- Deleting a row here does NOT delete the PDF from the library.

CREATE TABLE IF NOT EXISTS unverified_pdfs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_id        INTEGER NOT NULL UNIQUE,
    published_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    confirmed     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (pdf_id) REFERENCES pdfs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_unverified_pdfs_confirmed
    ON unverified_pdfs(confirmed);
CREATE INDEX IF NOT EXISTS idx_unverified_pdfs_published
    ON unverified_pdfs(published_at DESC);