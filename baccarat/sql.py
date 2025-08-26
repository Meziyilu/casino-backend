# baccarat/sql.py
import os
import psycopg

DDL = """
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
  day_key TEXT NOT NULL,      -- 'YYYY-MM-DD' (台北日)
  round_no INT NOT NULL,
  state TEXT NOT NULL DEFAULT 'betting', -- betting | locked | settled
  opened_at TIMESTAMPTZ DEFAULT now(),
  locked_at TIMESTAMPTZ,
  settled_at TIMESTAMPTZ,
  player_cards JSONB,         -- ["8H","3C","..."]
  banker_cards JSONB,
  player_total INT,
  banker_total INT,
  outcome TEXT                -- 'player' | 'banker' | 'tie'
);

-- 每日每房局號唯一
CREATE UNIQUE INDEX IF NOT EXISTS idx_rounds_unique ON rounds(room, day_key, round_no);
CREATE INDEX IF NOT EXISTS idx_rounds_lookup ON rounds(room, day_key, state);

CREATE TABLE IF NOT EXISTS bets (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id),
  room TEXT NOT NULL,
  day_key TEXT NOT NULL,
  round_no INT NOT NULL,
  side TEXT NOT NULL,         -- 'player' | 'banker' | 'tie'
  amount BIGINT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bets_lookup ON bets(room, day_key, round_no);
"""

def _conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(dsn, autocommit=True)

def ensure_schema():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(DDL)
