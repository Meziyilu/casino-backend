# baccarat/sql.py
import os
import psycopg

BASE_CREATE = """
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  nickname TEXT,
  balance BIGINT DEFAULT 0,
  is_admin BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rounds (
  id BIGSERIAL PRIMARY KEY,
  room TEXT NOT NULL,
  day_key TEXT NOT NULL,
  round_no INT NOT NULL
);

CREATE TABLE IF NOT EXISTS bets (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id),
  room TEXT NOT NULL,
  day_key TEXT NOT NULL,
  round_no INT NOT NULL,
  side TEXT NOT NULL,
  amount BIGINT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

# 逐欄位補齊（舊表也能安全執行）
ALTER_ROUNDS = [
    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'betting';",
    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS opened_at TIMESTAMPTZ;",
    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;",
    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ;",
    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS player_cards JSONB;",
    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS banker_cards JSONB;",
    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS player_total INT;",
    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS banker_total INT;",
    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS outcome TEXT;",
]

INDEXES = [
    # 每日每房局號唯一
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_rounds_unique ON rounds(room, day_key, round_no);",
    "CREATE INDEX IF NOT EXISTS idx_rounds_lookup ON rounds(room, day_key, state);",
    "CREATE INDEX IF NOT EXISTS idx_bets_lookup ON bets(room, day_key, round_no);",
]

def _conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(dsn, autocommit=True)

def ensure_schema():
    with _conn() as conn, conn.cursor() as cur:
        # 1) 基礎建表（如果不存在）
        cur.execute(BASE_CREATE)

        # 2) 舊表補欄位（關鍵！解你的錯）
        for sql in ALTER_ROUNDS:
            cur.execute(sql)

        # 3) 補索引
        for sql in INDEXES:
            cur.execute(sql)
