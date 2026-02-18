"""Database schema definitions and constants."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    token TEXT NOT NULL,
    nickname TEXT NOT NULL,
    special_code TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    last_activity TEXT NOT NULL,
    lang TEXT DEFAULT 'en',
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    is_premium INTEGER DEFAULT 0,
    banned INTEGER DEFAULT 0,
    ban_expires_at TEXT,
    last_revoke TEXT,
    protect_content INTEGER DEFAULT 0,
    messages_sent INTEGER DEFAULT 0,
    messages_received INTEGER DEFAULT 0,
    revoke_count INTEGER DEFAULT 0,
    allowed_types TEXT,
    avatar TEXT,
    frame TEXT
);

CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id INTEGER NOT NULL,
    blocked_user_id INTEGER NOT NULL,
    nickname TEXT NOT NULL,
    special_code TEXT,
    UNIQUE(recipient_id, blocked_user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    bot_msg_id INTEGER PRIMARY KEY,
    sender_id INTEGER NOT NULL,
    receiver_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_targets (
    sender_id INTEGER PRIMARY KEY,
    target_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS temp_links (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    max_uses INTEGER,
    current_uses INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS revoke_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    old_token TEXT NOT NULL,
    old_nickname TEXT NOT NULL,
    registered_at TEXT,
    revoked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_timestamps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webapp_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    receiver_id INTEGER NOT NULL,
    sender_nickname TEXT NOT NULL,
    message_text TEXT NOT NULL,
    message_type TEXT DEFAULT 'text',
    created_at TEXT NOT NULL,
    read INTEGER DEFAULT 0
);
"""

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_users_token ON users(token);",
    "CREATE INDEX IF NOT EXISTS idx_users_nickname ON users(nickname);",
    "CREATE INDEX IF NOT EXISTS idx_users_special_code ON users(special_code);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_profile_token ON users(profile_token);",
    "CREATE INDEX IF NOT EXISTS idx_blocks_recipient ON blocks(recipient_id);",
    "CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_pending_targets_created ON pending_targets(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_temp_links_user ON temp_links(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_message_timestamps_user_target ON message_timestamps(user_id, target_id);",
    "CREATE INDEX IF NOT EXISTS idx_webapp_messages_receiver ON webapp_messages(receiver_id, created_at);",
]
