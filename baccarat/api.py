# baccarat/api.py
import os, pytz, psycopg
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional

TZ = pytz.timezone("Asia/Taipei")
router = APIRouter()

def _conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(dsn, autocommit=True)

def day_key() -> str:
    return datetime.now(timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")

# ------ 公開查詢 ------
@router.get("/rooms")
def rooms():
    return {
        "rooms": [
            {"id": "room1", "seconds": 30},
            {"id": "room2", "seconds": 60},
            {"id": "room3", "seconds": 90},
        ],
        "tz": "Asia/Taipei",
    }

@router.get("/state")
def state(room: str):
    dk = day_key()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT round_no, state, opened_at, locked_at, settled_at FROM rounds WHERE room=%s AND day_key=%s ORDER BY round_no DESC LIMIT 1;",
            (room, dk),
        )
        row = cur.fetchone()
        if not row:
            return {"room": room, "day_key": dk, "round_no": 0, "state": "idle"}
        round_no, st, opened_at, locked_at, settled_at = row
        # 計算倒數（前端也可用這些時間自己算）
        return {
            "room": room, "day_key": dk, "round_no": round_no, "state": st,
            "opened_at": opened_at, "locked_at": locked_at, "settled_at": settled_at
        }

@router.get("/history")
def history(room: str, limit: int = 10):
    dk = day_key()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT round_no, outcome, player_total, banker_total, player_cards, banker_cards, settled_at
            FROM rounds
            WHERE room=%s AND day_key=%s AND state='settled'
            ORDER BY round_no DESC
            LIMIT %s;
            """,
            (room, dk, limit),
        )
        out = []
        for r in cur.fetchall():
            out.append({
                "round_no": r[0],
                "outcome": r[1],
                "player_total": r[2],
                "banker_total": r[3],
                "player_cards": r[4],
                "banker_cards": r[5],
                "settled_at": r[6],
            })
        return {"room": room, "day_key": dk, "items": out}

@router.get("/reveal")
def reveal(room: str):
    dk = day_key()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT round_no, outcome, player_total, banker_total, player_cards, banker_cards
            FROM rounds
            WHERE room=%s AND day_key=%s
            ORDER BY round_no DESC
            LIMIT 1;
            """,
            (room, dk),
        )
        row = cur.fetchone()
        if not row:
            return {}
        return {
            "round_no": row[0], "outcome": row[1],
            "player_total": row[2], "banker_total": row[3],
            "player_cards": row[4], "banker_cards": row[5],
        }

# ------ 下注 ------
class BetIn(BaseModel):
    room: str
    side: str = Field(pattern="^(player|banker|tie)$")
    amount: int = Field(gt=0)

def _user_from_header(x_user_id: Optional[str]) -> int:
    if not x_user_id:
        raise HTTPException(401, "X-User-Id required")
    try:
        return int(x_user_id)
    except:
        raise HTTPException(400, "Bad X-User-Id")

@router.post("/bet")
def bet(payload: BetIn, x_user_id: Optional[str] = Header(None)):
    uid = _user_from_header(x_user_id)
    dk = day_key()
    with _conn() as conn, conn.cursor() as cur:
        # 取得當前局
        cur.execute(
            "SELECT round_no, state FROM rounds WHERE room=%s AND day_key=%s ORDER BY round_no DESC LIMIT 1;",
            (payload.room, dk),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, "No active round")
        round_no, st = row
        if st != "betting":
            raise HTTPException(400, "Betting closed")

        # 扣款
        cur.execute("SELECT balance FROM users WHERE id=%s;", (uid,))
        row2 = cur.fetchone()
        if not row2:
            raise HTTPException(400, "User not found")
        bal = int(row2[0])
        if bal < payload.amount:
            raise HTTPException(400, "Insufficient balance")
        cur.execute("UPDATE users SET balance = balance - %s WHERE id=%s;", (payload.amount, uid))

        # 記錄注單
        cur.execute(
            """
            INSERT INTO bets (user_id, room, day_key, round_no, side, amount)
            VALUES (%s,%s,%s,%s,%s,%s);
            """,
            (uid, payload.room, dk, round_no, payload.side, payload.amount),
        )

        return {"ok": True, "room": payload.room, "round_no": round_no, "side": payload.side, "amount": payload.amount}

# ------ Admin 清理 ------
from fastapi import Query

@router.post("/admin/cleanup")
def admin_cleanup(mode: str = Query("before_today", pattern="^(all|before_today)$")):
    token = os.getenv("ADMIN_TOKEN", "")
    if not token:
        raise HTTPException(500, "ADMIN_TOKEN not set")
    # 這裡為簡化，直接用環境變數比對。若要 header，可改讀 x-admin-token。
    # ex: x_admin_token: str = Header(None)
    dk = day_key()
    with _conn() as conn, conn.cursor() as cur:
        if mode == "all":
            cur.execute("DELETE FROM bets;")
            cur.execute("DELETE FROM rounds;")
        else:
            cur.execute("DELETE FROM bets WHERE day_key <> %s;", (dk,))
            cur.execute("DELETE FROM rounds WHERE day_key <> %s;", (dk,))
    return {"ok": True, "mode": mode}
