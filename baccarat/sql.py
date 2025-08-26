# baccarat/sql.py
import os
import psycopg

DATABASE_URL = os.getenv("DATABASE_URL")

def db():
    return psycopg.connect(DATABASE_URL, autocommit=True)

def ensure_schema():
    with db() as c:
        cur = c.cursor()

        # 使用者（保守版，存在就不動）
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
          id SERIAL PRIMARY KEY,
          username TEXT UNIQUE,
          password_hash TEXT,
          nickname TEXT,
          balance BIGINT DEFAULT 0,
          created_at TIMESTAMPTZ DEFAULT now()
        );
        """)

        # 局表：room + day_key + round_no 唯一；locked/settled 控制投注/結算階段
        cur.execute("""
        CREATE TABLE IF NOT EXISTS rounds(
          id BIGSERIAL PRIMARY KEY,
          room TEXT NOT NULL,
          day_key DATE NOT NULL,
          round_no INT NOT NULL,
          opened_at TIMESTAMPTZ DEFAULT now(),
          locked BOOLEAN DEFAULT FALSE,
          settled BOOLEAN DEFAULT FALSE,
          player_cards TEXT,     -- "A,9,6"
          banker_cards TEXT,     -- "K,2"
          player_total INT,
          banker_total INT,
          winner TEXT,           -- 'player'|'banker'|'tie'
          UNIQUE(room, day_key, round_no)
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rounds_room_day ON rounds(room, day_key);")

        # 下注表
        cur.execute("""
        CREATE TABLE IF NOT EXISTS bets(
          id BIGSERIAL PRIMARY KEY,
          user_id INT NOT NULL,
          room TEXT NOT NULL,
          day_key DATE NOT NULL,
          round_no INT NOT NULL,
          side TEXT NOT NULL,        -- 'player'|'banker'|'tie'
          amount BIGINT NOT NULL,
          created_at TIMESTAMPTZ DEFAULT now()
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_room_day_round ON bets(room, day_key, round_no);")
