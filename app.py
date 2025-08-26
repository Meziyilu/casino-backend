import os
import time
import random
import threading
from math import floor
from typing import Optional, List, Dict

import psycopg
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ===== 基本設定 =====
APP_NAME = "Casino Backend"
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env")

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme")

# 允許的前端網域（按你的實際情況調整）
ALLOWED_ORIGINS = [
    "https://topz0705.com",
    "https://casino-frontend-pya7.onrender.com",
]

TPE = ZoneInfo("Asia/Taipei")  # 台北時區
ROOM_CONFIG: Dict[str, Dict] = {
    "room1": {"bet_seconds": 60, "reveal_seconds": 15},
    "room2": {"bet_seconds": 60, "reveal_seconds": 15},
    "room3": {"bet_seconds": 60, "reveal_seconds": 15},
}

# ===== FastAPI =====
app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== 工具函式 =====
def today_tpe_date():
    """回傳今天台北日曆日期 (date)"""
    return datetime.now(TPE).date()


def get_conn():
    return psycopg.connect(DATABASE_URL)


# ===== 資料庫初始化 =====
def init_db():
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
          id SERIAL PRIMARY KEY,
          username TEXT UNIQUE NOT NULL,
          password TEXT NOT NULL,
          balance BIGINT DEFAULT 1000,
          is_admin BOOLEAN DEFAULT false,
          created_at TIMESTAMPTZ DEFAULT now()
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS rounds (
          id BIGSERIAL PRIMARY KEY,
          room TEXT NOT NULL,
          round_no INT NOT NULL,
          day_key DATE,                    -- 台北日期（每日重置用）
          opened_at TIMESTAMPTZ DEFAULT now(),
          closed_at TIMESTAMPTZ,
          player_total INT,
          banker_total INT,
          outcome TEXT,                    -- player | banker | tie
          player_draw3 BOOLEAN DEFAULT false,
          banker_draw3 BOOLEAN DEFAULT false,
          player_cards JSONB,              -- ["A♠","9♥","3♦"]
          banker_cards JSONB
        );
        """)
        # 可能已存在，保險 ALTER
        cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS day_key DATE;")
        cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;")
        cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS player_draw3 BOOLEAN DEFAULT false;")
        cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS banker_draw3 BOOLEAN DEFAULT false;")
        cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS player_cards JSONB;")
        cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS banker_cards JSONB;")

        # 每日、房間、局號唯一
        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_round_room_day_no
        ON rounds(room, day_key, round_no);
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rounds_closed_at ON rounds(closed_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rounds_opened_at ON rounds(opened_at);")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS bets (
          id BIGSERIAL PRIMARY KEY,
          user_id INT REFERENCES users(id),
          room TEXT NOT NULL,
          round_no INT NOT NULL,
          day_key DATE,
          side TEXT,                       -- player | banker | tie | future side-bets
          amount BIGINT NOT NULL,
          created_at TIMESTAMPTZ DEFAULT now()
        );
        """)
        cur.execute("ALTER TABLE bets ADD COLUMN IF NOT EXISTS day_key DATE;")
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_bets_room_day_round
        ON bets(room, day_key, round_no);
        """)

        conn.commit()


init_db()

# ===== 資料模型 =====
class Register(BaseModel):
    username: str
    password: str


class Login(BaseModel):
    username: str
    password: str


class BetReq(BaseModel):
    side: str
    amount: int


# ===== Auth / User =====
def fetch_user(token: Optional[str]):
    if not token or not token.startswith("user-"):
        raise HTTPException(401, "Invalid token")
    try:
        uid = int(token.split("-")[1])
    except Exception:
        raise HTTPException(401, "Invalid token")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, username, balance, is_admin FROM users WHERE id=%s", (uid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(401, "User not found")
        return {"id": row[0], "username": row[1], "balance": row[2], "is_admin": row[3]}


@app.post("/auth/register")
def register(data: Register):
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (username,password) VALUES (%s,%s) RETURNING id",
                (data.username, data.password),
            )
            conn.commit()
        except Exception:
            raise HTTPException(409, "Username already exists")
    return {"ok": True}


@app.post("/auth/login")
def login(data: Login):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, password, is_admin FROM users WHERE username=%s", (data.username,))
        row = cur.fetchone()
        if not row or row[1] != data.password:
            raise HTTPException(401, "Invalid credentials")
        return {"token": f"user-{row[0]}", "is_admin": row[2]}


@app.get("/me")
def me(token: Optional[str] = Query(default=None)):
    return fetch_user(token)


@app.get("/balance")
def balance(token: Optional[str] = Query(default=None)):
    u = fetch_user(token)
    return {"balance": u["balance"]}


# ===== 百家樂牌局邏輯（真實補牌表） =====
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]


def draw_card() -> str:
    return random.choice(RANKS) + random.choice(SUITS)


def card_value(rank: str) -> int:
    if rank in ("10", "J", "Q", "K"):
        return 0
    if rank == "A":
        return 1
    return int(rank)


def total(cards: List[str]) -> int:
    v = 0
    for c in cards:
        v += card_value(c[:-1])
    return v % 10


def deal_baccarat_hand():
    """
    依賭場補牌規則：
    - 閒先：0-5 補，6-7 停，8/9 天牌終止
    - 莊後：依莊點數與閒第三張而定（標準表）
    回傳：
      {
        "player_cards": [...],
        "banker_cards": [...],
        "player_total": int,
        "banker_total": int,
        "player_draw3": bool,
        "banker_draw3": bool,
        "outcome": "player"|"banker"|"tie"
      }
    """
    p = [draw_card(), draw_card()]
    b = [draw_card(), draw_card()]

    pt = total(p)
    bt = total(b)

    # 天牌
    if pt in (8, 9) or bt in (8, 9):
        outcome = "tie"
        if pt > bt:
            outcome = "player"
        elif bt > pt:
            outcome = "banker"
        return {
            "player_cards": p,
            "banker_cards": b,
            "player_total": pt,
            "banker_total": bt,
            "player_draw3": False,
            "banker_draw3": False,
            "outcome": outcome,
        }

    # 閒家第三張
    p3 = False
    third = None
    if pt <= 5:
        third = draw_card()
        p.append(third)
        p3 = True
        pt = total(p)

    # 莊家第三張（依表）
    b3 = False
    if not p3:
        # 閒未補：莊 0-5 補，6-7 停
        if bt <= 5:
            b.append(draw_card())
            b3 = True
            bt = total(b)
    else:
        # 閒有第三張 → 看第三張 rank 對應值
        t = card_value(third[:-1])
        if bt <= 2:
            b.append(draw_card()); b3 = True; bt = total(b)
        elif bt == 3 and t != 8:
            b.append(draw_card()); b3 = True; bt = total(b)
        elif bt == 4 and 2 <= t <= 7:
            b.append(draw_card()); b3 = True; bt = total(b)
        elif bt == 5 and 4 <= t <= 7:
            b.append(draw_card()); b3 = True; bt = total(b)
        elif bt == 6 and t in (6, 7):
            b.append(draw_card()); b3 = True; bt = total(b)
        # bt == 7 停

    outcome = "tie"
    if pt > bt:
        outcome = "player"
    elif bt > pt:
        outcome = "banker"

    return {
        "player_cards": p,
        "banker_cards": b,
        "player_total": pt,
        "banker_total": bt,
        "player_draw3": p3,
        "banker_draw3": b3,
        "outcome": outcome,
    }


# ===== 下注 =====
@app.post("/bet")
def bet(
    data: BetReq,
    room: str = Query("room1"),
    token: Optional[str] = Query(default=None),
):
    if room not in ROOM_CONFIG:
        raise HTTPException(400, "Invalid room")
    if data.amount <= 0:
        raise HTTPException(400, "Invalid amount")
    if data.side not in ("player", "banker", "tie"):
        raise HTTPException(400, "Invalid side")

    u = fetch_user(token)
    day_key = today_tpe_date()

    with get_conn() as conn:
        cur = conn.cursor()

        # 只找今天最後一局
        cur.execute(
            """
            SELECT round_no, outcome, opened_at
            FROM rounds
            WHERE room=%s AND day_key=%s
            ORDER BY round_no DESC LIMIT 1
            """,
            (room, day_key),
        )
        r = cur.fetchone()
        if not r:
            raise HTTPException(400, "No round")
        round_no, outcome, opened_at = r
        if outcome is not None:
            raise HTTPException(400, "Round already closed")

        # 檢查是否仍在下注時間
        bet_seconds = ROOM_CONFIG[room]["bet_seconds"]
        remain = bet_seconds - int((datetime.now(timezone.utc) - opened_at).total_seconds())
        if remain <= 0:
            raise HTTPException(400, "Betting closed")

        # 餘額
        cur.execute("SELECT balance FROM users WHERE id=%s", (u["id"],))
        bal = cur.fetchone()[0]
        if bal < data.amount:
            raise HTTPException(400, "Insufficient balance")

        # 扣款 + 建立下注
        cur.execute("UPDATE users SET balance=balance-%s WHERE id=%s", (data.amount, u["id"]))
        cur.execute(
            """
            INSERT INTO bets (user_id, room, round_no, day_key, side, amount)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (u["id"], room, round_no, day_key, data.side, data.amount),
        )
        conn.commit()

    return {"ok": True}


# ===== 查局況 / 歷史 =====
@app.get("/rounds/current")
def current(room: str = Query("room1")):
    if room not in ROOM_CONFIG:
        raise HTTPException(400, "Invalid room")
    day_key = today_tpe_date()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT round_no, outcome, opened_at, closed_at
            FROM rounds
            WHERE room=%s AND day_key=%s
            ORDER BY round_no DESC LIMIT 1
            """,
            (room, day_key),
        )
        r = cur.fetchone()
        if not r:
            return {"status": "idle"}
        round_no, outcome, opened_at, closed_at = r
        status = "closed" if outcome else "open"
        remain = 0
        if not outcome:
            bet_seconds = ROOM_CONFIG[room]["bet_seconds"]
            remain = bet_seconds - int((datetime.now(timezone.utc) - opened_at).total_seconds())
            if remain < 0:
                remain = 0
        return {"round_no": round_no, "status": status, "remain_sec": remain}


@app.get("/rounds/last10")
def last10(room: str = Query("room1"), today_only: int = Query(1)):
    """預設僅顯示今日，?today_only=0 可看全部近 10 局"""
    params = [room]
    where = "room=%s"
    if today_only:
        where += " AND day_key=%s"
        params.append(today_tpe_date())
    sql = f"""
        SELECT round_no, opened_at, player_total, banker_total, outcome,
               player_draw3, banker_draw3, player_cards, banker_cards
        FROM rounds
        WHERE {where}
        ORDER BY day_key DESC, round_no DESC
        LIMIT 10
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = []
        for r in cur.fetchall():
            rows.append({
                "round_no": r[0],
                "opened_at": r[1].isoformat() if r[1] else None,
                "player_total": r[2],
                "banker_total": r[3],
                "outcome": r[4],
                "player_draw3": r[5],
                "banker_draw3": r[6],
                "player_cards": r[7],
                "banker_cards": r[8],
            })
        return {"rows": rows}


# ===== 今日排行榜（台北時間） =====
@app.get("/leaderboard/daily")
def leaderboard_daily(limit: int = 5):
    tz = TPE
    now_tpe = datetime.now(tz)
    day_start_tpe = now_tpe.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = day_start_tpe.astimezone(timezone.utc)

    sql = """
    WITH recent AS (
      SELECT b.user_id, b.amount, b.side, r.outcome
      FROM bets b
      JOIN rounds r
        ON r.room=b.room AND r.day_key=b.day_key AND r.round_no=b.round_no
      WHERE COALESCE(r.closed_at, r.opened_at) >= %s
    ),
    profit_calc AS (
      SELECT user_id,
        CASE
          WHEN outcome='player' AND side='player' THEN amount
          WHEN outcome='banker' AND side='banker' THEN FLOOR(amount*0.95)
          WHEN outcome='tie'    AND side='tie'    THEN amount*8
          ELSE -amount
        END AS profit
      FROM recent
    )
    SELECT u.username, COALESCE(SUM(p.profit),0)::BIGINT AS profit
    FROM profit_calc p
    JOIN users u ON u.id = p.user_id
    GROUP BY u.username
    ORDER BY COALESCE(SUM(p.profit),0) DESC, u.username ASC
    LIMIT %s;
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (day_start_utc, limit))
        rows = [{"username": r[0], "profit": int(r[1])} for r in cur.fetchall()]

    return {"tz": "Asia/Taipei", "day_start": day_start_tpe.isoformat(), "rows": rows}


# ===== 自動荷官迴圈（每日 00:00 重置局號） =====
def dealer_loop(room: str):
    cfg = ROOM_CONFIG[room]
    cur_day = today_tpe_date()
    round_no = 0

    while True:
        try:
            # 跨日：重置
            now_day = today_tpe_date()
            if now_day != cur_day:
                cur_day = now_day
                round_no = 0

            # 開新局
            with get_conn() as conn:
                cur = conn.cursor()
                round_no += 1
                cur.execute(
                    "INSERT INTO rounds (room, day_key, round_no) VALUES (%s,%s,%s)",
                    (room, cur_day, round_no),
                )
                conn.commit()

            # 下注時間
            time.sleep(cfg["bet_seconds"])

            # 發牌 + 補牌
            res = deal_baccarat_hand()

            # 結算/寫入
            with get_conn() as conn:
                cur = conn.cursor()

                cur.execute(
                    """
                    UPDATE rounds
                    SET player_total=%s, banker_total=%s, outcome=%s, closed_at=now(),
                        player_draw3=%s, banker_draw3=%s,
                        player_cards=%s, banker_cards=%s
                    WHERE room=%s AND day_key=%s AND round_no=%s
                    """,
                    (
                        res["player_total"],
                        res["banker_total"],
                        res["outcome"],
                        res["player_draw3"],
                        res["banker_draw3"],
                        res["player_cards"],
                        res["banker_cards"],
                        room,
                        cur_day,
                        round_no,
                    ),
                )

                # 派彩（主注）
                cur.execute(
                    """
                    SELECT user_id, side, amount
                    FROM bets
                    WHERE room=%s AND day_key=%s AND round_no=%s
                    """,
                    (room, cur_day, round_no),
                )
                for uid, side, amt in cur.fetchall():
                    win = 0
                    if side == "player" and res["outcome"] == "player":
                        win = amt * 2
                    elif side == "banker" and res["outcome"] == "banker":
                        win = floor(amt * 1.95)
                    elif side == "tie" and res["outcome"] == "tie":
                        win = amt * 9
                    if win > 0:
                        cur.execute("UPDATE users SET balance=balance+%s WHERE id=%s", (win, uid))

                conn.commit()

            # 開牌展示時間
            time.sleep(cfg["reveal_seconds"])

        except Exception as e:
            print(f"Dealer loop error [{room}]:", e)
            time.sleep(5)


# 啟動三個房間的自動流程
for r in ROOM_CONFIG.keys():
    threading.Thread(target=dealer_loop, args=(r,), daemon=True).start()


# ===== 管理清理 API（刪除歷史） =====
@app.post("/admin/purge")
def admin_purge(
    scope: str = Query(example="before_today", description="'before_today' 或 'all'"),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    """
    清理歷史資料：
      - scope = 'before_today'：刪除「今日以前」的 rounds/bets
      - scope = 'all'         ：刪除所有 rounds/bets（請謹慎）
    需要 HTTP Header: X-Admin-Token = ADMIN_TOKEN
    """
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Unauthorized")

    with get_conn() as conn:
        cur = conn.cursor()
        if scope == "before_today":
            dk = today_tpe_date()
            cur.execute("DELETE FROM bets WHERE day_key < %s", (dk,))
            cur.execute("DELETE FROM rounds WHERE day_key < %s", (dk,))
        elif scope == "all":
            cur.execute("TRUNCATE bets RESTART IDENTITY CASCADE;")
            cur.execute("TRUNCATE rounds RESTART IDENTITY CASCADE;")
        else:
            raise HTTPException(400, "Invalid scope")

        conn.commit()
    return {"ok": True, "scope": scope}
