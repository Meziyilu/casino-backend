# baccarat/api.py
from fastapi import APIRouter, HTTPException, Depends, Header, Query
from pydantic import BaseModel
import os
from util.db import db
from auth.api import require_user
from .sql import today_key, ensure_schema
from .service import current_room_state

router = APIRouter()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin-dev")

# ---- 輔助 ----
def require_admin(x_admin_token: str | None = Header(default=None)):
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "not admin")
    return True

# ---- 請求模型 ----
class BetBody(BaseModel):
    room: str
    side: str  # player/banker/tie
    amount: int

# ---- 下注 ----
@router.post("/bet")
def place_bet(body: BetBody, user=Depends(require_user)):
    uid, _ = user
    room = body.room
    side = body.side
    amount = int(body.amount)

    if room not in ("room1", "room2", "room3"):
        raise HTTPException(422, "invalid room")
    if side not in ("player", "banker", "tie"):
        raise HTTPException(422, "invalid side")
    if amount <= 0:
        raise HTTPException(422, "invalid amount")

    ensure_schema()
    with db() as conn, conn.cursor() as cur:
        # 找當前局
        cur.execute("""
          SELECT round_no, phase FROM rounds
          WHERE day_key=%s AND room=%s
          ORDER BY round_no DESC LIMIT 1;
        """, (today_key(), room))
        r = cur.fetchone()
        if not r or r["phase"] != "betting":
            raise HTTPException(409, "not betting phase")

        rn = r["round_no"]
        # 扣款 & 建立下注（原子性）
        cur.execute("SELECT balance FROM users WHERE id=%s FOR UPDATE;", (uid,))
        bal = cur.fetchone()["balance"]
        if bal < amount:
            raise HTTPException(402, "insufficient balance")

        cur.execute("UPDATE users SET balance = balance - %s WHERE id=%s;", (amount, uid))
        cur.execute("""
          INSERT INTO bets (user_id, day_key, room, round_no, side, amount)
          VALUES (%s,%s,%s,%s,%s,%s);
        """, (uid, today_key(), room, rn, side, amount))
        conn.commit()

    return {"ok": True, "room": room, "round_no": rn}

# ---- 房態 ----
@router.get("/state")
def state(room: str = Query(..., pattern="^room[123]$")):
    return current_room_state(room)

# ---- 歷史（近 N 局 / 今日）----
@router.get("/history")
def history(room: str = Query(..., pattern="^room[123]$"), limit: int = 10):
    ensure_schema()
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
          SELECT round_no, outcome, player_total, banker_total
          FROM rounds
          WHERE day_key=%s AND room=%s AND outcome IS NOT NULL
          ORDER BY round_no DESC
          LIMIT %s;
        """, (today_key(), room, limit))
        rows = cur.fetchall()
    return {"room": room, "rows": rows}

# ---- 今日排行榜（前 5）----
@router.get("/leaderboard/today")
def leaderboard_today():
    # 以今日所有已結算局，計算每用戶 profit = 得到金額 - 下注本金
    # 規則：player 贏 1:1，banker 贏 0.95，tie 贏 8:1，遇 tie 時 player/banker push
    ensure_schema()
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
          WITH base AS (
            SELECT b.user_id, b.side, b.amount, r.outcome
            FROM bets b
            JOIN rounds r ON r.day_key=b.day_key AND r.room=b.room AND r.round_no=b.round_no
            WHERE b.day_key=%s AND r.phase='settled'
          ), pay AS (
            SELECT user_id,
              SUM(
                CASE
                  WHEN outcome='player' AND side='player' THEN amount -- 淨利：中獎拿回 2*amount，扣掉本金 = +amount
                  WHEN outcome='banker' AND side='banker' THEN CAST(ROUND(amount*0.95) AS BIGINT)
                  WHEN outcome='tie' AND side='tie' THEN amount*8
                  WHEN outcome='tie' AND side IN ('player','banker') THEN 0     -- push，淨利 0
                  ELSE -amount                                                -- 輸的話，淨利= -本金
                END
              ) AS profit
            FROM base
            GROUP BY user_id
          )
          SELECT u.username, COALESCE(u.nickname, u.username) AS nickname,
                 COALESCE(p.profit,0) AS profit
          FROM users u
          JOIN pay p ON p.user_id=u.id
          ORDER BY profit DESC
          LIMIT 5;
        """, (today_key(),))
        rows = cur.fetchall()
    return {"top": rows}

# ---- Admin：發幣 / 清理 / 查餘額 ----
class GrantBody(BaseModel):
    username: str
    amount: int

class CleanupBody(BaseModel):
    mode: str  # today | all

@router.post("/admin/grant")
def admin_grant(body: GrantBody, ok=Depends(require_admin)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username=%s;", (body.username,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "user not found")
        uid = r["id"]
        cur.execute("UPDATE users SET balance = balance + %s WHERE id=%s;", (int(body.amount), uid))
        conn.commit()
    return {"ok": True}

@router.post("/admin/cleanup")
def admin_cleanup(body: CleanupBody, ok=Depends(require_admin)):
    if body.mode not in ("today", "all"):
        raise HTTPException(422, "invalid mode")
    with db() as conn, conn.cursor() as cur:
        if body.mode == "today":
            # 刪除「今天以前」的資料
            cur.execute("DELETE FROM bets WHERE day_key < %s;", (today_key(),))
            cur.execute("DELETE FROM rounds WHERE day_key < %s;", (today_key(),))
        else:
            # 全部清掉（不動 users）
            cur.execute("TRUNCATE TABLE bets;")
            cur.execute("TRUNCATE TABLE rounds;")
        conn.commit()
    return {"ok": True}

@router.get("/admin/balance")
def admin_balance(username: str, ok=Depends(require_admin)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT balance FROM users WHERE username=%s;", (username,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "user not found")
        return {"username": username, "balance": r["balance"]}
