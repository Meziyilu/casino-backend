# baccarat/sql.py
from util.db import db
from datetime import datetime, timezone
import pytz

TZ = pytz.timezone("Asia/Taipei")

BETTING_SECONDS = {
    "room1": 30,
    "room2": 60,
    "room3": 90,
}

ROOMS = list(BETTING_SECONDS.keys())

def taipei_now():
    return datetime.now(TZ)

def today_key():
    # 以台北時區的當天（日期）當 day_key
    return taipei_now().date()

def ensure_schema():
    with db() as conn, conn.cursor() as cur:
        # users 已在 auth 確保，這裡只確保索引
        cur.execute("""
        CREATE TABLE IF NOT EXISTS rounds (
          id BIGSERIAL PRIMARY KEY,
          day_key DATE NOT NULL,
          room TEXT NOT NULL,
          round_no INT NOT NULL,
          opened_at TIMESTAMPTZ DEFAULT now(),
          phase TEXT, -- betting | reveal | settled
          player_total INT,
          banker_total INT,
          player_draw3 BOOLEAN,
          banker_draw3 BOOLEAN,
          outcome TEXT -- player | banker | tie
        );
        """)
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uniq_round ON rounds (day_key, room, round_no);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_round_room_day ON rounds (room, day_key, round_no);")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS bets (
          id BIGSERIAL PRIMARY KEY,
          user_id INT REFERENCES users(id),
          day_key DATE NOT NULL,
          room TEXT NOT NULL,
          round_no INT NOT NULL,
          side TEXT NOT NULL,  -- player | banker | tie
          amount BIGINT NOT NULL,
          created_at TIMESTAMPTZ DEFAULT now()
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_key ON bets (day_key, room, round_no);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_user ON bets (user_id, created_at);")

        # Leaderboard 用（快取淨利，可選）
        cur.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_today_profit AS
        SELECT 0 as user_id, 0 as profit
        WITH NO DATA;
        """)
        conn.commit()

def current_round_info(cur, room: str):
    cur.execute("""
      SELECT round_no, phase, opened_at, player_total, banker_total, player_draw3, banker_draw3, outcome
      FROM rounds
      WHERE day_key=%s AND room=%s
      ORDER BY round_no DESC
      LIMIT 1;
    """, (today_key(), room))
    return cur.fetchone()

def next_round_no(cur, room: str) -> int:
    cur.execute("""
      SELECT COALESCE(MAX(round_no), 0) AS m
      FROM rounds
      WHERE day_key=%s AND room=%s;
    """, (today_key(), room))
    m = cur.fetchone()["m"]
    return int(m) + 1

def room_pools(cur, room: str, round_no: int):
    cur.execute("""
      SELECT side, SUM(amount)::BIGINT AS total, COUNT(*) AS cnt
      FROM bets
      WHERE day_key=%s AND room=%s AND round_no=%s
      GROUP BY side;
    """, (today_key(), room, round_no))
    res = {"player": 0, "banker": 0, "tie": 0}
    bettors = 0
    for r in cur.fetchall():
        res[r["side"]] = int(r["total"] or 0)
        bettors += int(r["cnt"] or 0)
    return res, bettors
