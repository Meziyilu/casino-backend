# baccarat/api.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import jwt
import psycopg
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

router = APIRouter()

# 三個房間
ROOMS = ["room1", "room2", "room3"]

# ===== Helpers =====

def get_conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    # autocommit=True 方便 API 中執行簡短查詢
    return psycopg.connect(dsn, autocommit=True)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def decode_token_get_uid(authorization: Optional[str] = Header(None)) -> int:
    """
    從 Authorization: Bearer <jwt> 解析出 uid
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    token = authorization.split(" ", 1)[1]
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        raise HTTPException(status_code=500, detail="server secret not configured")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        uid = int(payload.get("uid"))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    return uid

# ====== LOBBY ROOMS 列表 ======

@router.get("/rooms")
def list_rooms() -> Dict[str, List[Dict[str, Any]]]:
    """
    回傳三個房間的最新一局狀態:
      room, round_no, phase('betting'|'revealing'|'waiting'), seconds_left, totals{player,banker,tie}
    """
    out: List[Dict[str, Any]] = []
    now = now_utc()
    with get_conn() as conn, conn.cursor() as cur:
        for room in ROOMS:
            # 取該房最新的一局
            cur.execute(
                """
                SELECT id, round_no, room, close_at
                FROM rounds
                WHERE room = %s
                ORDER BY id DESC
                LIMIT 1;
                """,
                (room,),
            )
            row = cur.fetchone()
            if not row:
                out.append({
                    "room": room,
                    "round_no": 0,
                    "phase": "waiting",
                    "seconds_left": 0,
                    "totals": {"player": 0, "banker": 0, "tie": 0},
                })
                continue

            rid, round_no, _room, close_at = row
            # phase / seconds_left
            if close_at and now < close_at:
                phase = "betting"
                seconds_left = int((close_at - now).total_seconds())
                if seconds_left < 0: seconds_left = 0
            else:
                phase = "revealing"
                seconds_left = 0

            # 下注總額
            cur.execute(
                """
                SELECT side, COALESCE(SUM(amount),0)
                FROM bets
                WHERE room = %s AND round_no = %s
                GROUP BY side;
                """,
                (room, round_no),
            )
            sums = {"player": 0, "banker": 0, "tie": 0}
            for s, total in cur.fetchall() or []:
                if s in sums:
                    sums[s] = int(total)

            out.append({
                "room": room,
                "round_no": int(round_no),
                "phase": phase,
                "seconds_left": seconds_left,
                "totals": sums,
            })
    return {"rooms": out}

# ====== STATE（單房） ======

@router.get("/state")
def get_state(room: str = Query(..., description="room1|room2|room3")) -> Dict[str, Any]:
    if room not in ROOMS:
        raise HTTPException(400, detail="invalid room")
    now = now_utc()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, round_no, open_at, close_at, outcome,
                   COALESCE(player_total,0), COALESCE(banker_total,0)
            FROM rounds
            WHERE room = %s
            ORDER BY id DESC
            LIMIT 1;
            """,
            (room,),
        )
        row = cur.fetchone()
        if not row:
            # 尚無任何一局
            return {
                "room": room,
                "round_no": 0,
                "phase": "waiting",
                "seconds_left": 0,
                "outcome": None,
                "player_total": 0,
                "banker_total": 0,
                "server_time": now.isoformat(),
            }

        rid, round_no, open_at, close_at, outcome, player_total, banker_total = row
        if close_at and now < close_at:
            phase = "betting"
            seconds_left = int((close_at - now).total_seconds())
            if seconds_left < 0: seconds_left = 0
        else:
            phase = "revealing"
            seconds_left = 0

        return {
            "room": room,
            "round_no": int(round_no),
            "phase": phase,
            "seconds_left": seconds_left,
            "outcome": outcome,
            "player_total": int(player_total or 0),
            "banker_total": int(banker_total or 0),
            "server_time": now.isoformat(),
        }

# ====== BET 下單 ======

from pydantic import BaseModel, Field

class BetBody(BaseModel):
    room: str = Field(..., examples=["room1"])
    side: str = Field(..., pattern="^(player|banker|tie)$")
    amount: int = Field(..., ge=1)

@router.post("/bet")
def place_bet(
    body: BetBody,
    uid: int = Depends(decode_token_get_uid),
) -> Dict[str, Any]:
    """
    僅在 close_at 之前允許下注
    """
    if body.room not in ROOMS:
        raise HTTPException(400, detail="invalid room")
    now = now_utc()
    with get_conn() as conn, conn.cursor() as cur:
        # 找當前回合
        cur.execute(
            """
            SELECT round_no, close_at
            FROM rounds
            WHERE room = %s
            ORDER BY id DESC
            LIMIT 1;
            """,
            (body.room,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(409, detail="round not ready")
        round_no, close_at = row
        if not close_at or now >= close_at:
            raise HTTPException(409, detail="betting closed")

        # 寫入下注
        cur.execute(
            """
            INSERT INTO bets(user_id, room, round_no, side, amount, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW());
            """,
            (uid, body.room, int(round_no), body.side, int(body.amount)),
        )
        return {"ok": True, "room": body.room, "round_no": int(round_no)}

# ====== HISTORY（近十局） ======

@router.get("/history")
def history(
    room: str = Query(...),
    limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    if room not in ROOMS:
        raise HTTPException(400, detail="invalid room")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT round_no, outcome, player_total, banker_total, open_at
            FROM rounds
            WHERE room = %s AND outcome IS NOT NULL
            ORDER BY round_no DESC
            LIMIT %s;
            """,
            (room, limit),
        )
        rows = cur.fetchall() or []
        rows = [
            {
                "round_no": int(r[0]),
                "outcome": r[1],
                "player_total": int(r[2] or 0),
                "banker_total": int(r[3] or 0),
                "open_at": (r[4].isoformat() if r[4] else None),
            }
            for r in rows
        ]
        rows.reverse()  # 從舊到新顯示
        return {"room": room, "items": rows}

# ====== 今日排行榜 ======

@router.get("/leaderboard/today")
def leaderboard_today() -> Dict[str, Any]:
    """
    以「今日已結算的淨利」排行 (台北時間 00:00 起算)
    * 這裡假設你有在結算時把每注的贏輸寫回 bets 的某欄位 (例如 settle_amount)，
      若尚未實作派彩欄位，可以先回傳今日下注總額排行榜或留空實作。
    """
    # 先求台北今日 00:00 的 UTC 時間
    import pytz
    tz = pytz.timezone("Asia/Taipei")
    today_tpe = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = today_tpe.astimezone(timezone.utc)

    with get_conn() as conn, conn.cursor() as cur:
        # 若你還沒 settle 欄位，可改成 SUM(amount) 當示意
        # 這裡示範以下注總額排行：
        cur.execute(
            """
            SELECT b.user_id, COALESCE(SUM(b.amount),0) AS total
            FROM bets b
            WHERE b.created_at >= %s
            GROUP BY b.user_id
            ORDER BY total DESC
            LIMIT 5;
            """,
            (start_utc,),
        )
        tops = cur.fetchall() or []

        # 取使用者暱稱
        out = []
        for uid, total in tops:
            cur.execute("SELECT nickname FROM users WHERE id=%s;", (uid,))
            nick = (cur.fetchone() or [None])[0] or f"user_{uid}"
            out.append({
                "user_id": int(uid),
                "nickname": nick,
                "value": int(total),
            })
        return {"top5": out}

# ====== 管理 API ======

def require_admin(x_admin_token: Optional[str] = Header(None)) -> None:
    admin_token = os.environ.get("ADMIN_TOKEN")
    if not admin_token or not x_admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=401, detail="admin token invalid")

class GrantBody(BaseModel):
    username: str
    amount: int = Field(..., ge=1)

@router.post("/admin/grant")
def admin_grant(body: GrantBody, _: None = Depends(require_admin)) -> Dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username=%s;", (body.username,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, detail="user not found")
        uid = row[0]
        cur.execute("UPDATE users SET balance = COALESCE(balance,0) + %s WHERE id=%s;", (int(body.amount), uid))
        return {"ok": True, "username": body.username, "granted": int(body.amount)}

@router.get("/admin/balance")
def admin_balance(username: str = Query(...), _: None = Depends(require_admin)) -> Dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, nickname, COALESCE(balance,0) FROM users WHERE username=%s;", (username,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, detail="user not found")
        uid, nick, bal = row
        return {"user_id": int(uid), "username": username, "nickname": nick, "balance": int(bal)}

class CleanupBody(BaseModel):
    mode: str = Field(..., pattern="^(today_or_older|all)$")

@router.post("/admin/cleanup")
def admin_cleanup(body: CleanupBody, _: None = Depends(require_admin)) -> Dict[str, Any]:
    """
    today_or_older: 刪除「今天以前」歷史
    all: 刪除全部 rounds/bets
    """
    deleted = {"rounds": 0, "bets": 0}
    with get_conn() as conn, conn.cursor() as cur:
        if body.mode == "all":
            cur.execute("DELETE FROM bets;")
            deleted["bets"] = cur.rowcount or 0
            cur.execute("DELETE FROM rounds;")
            deleted["rounds"] = cur.rowcount or 0
        else:
            # 計算台北今日 00:00 的 UTC
            import pytz
            tz = pytz.timezone("Asia/Taipei")
            today_tpe = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            start_utc = today_tpe.astimezone(timezone.utc)

            cur.execute("DELETE FROM bets WHERE created_at < %s;", (start_utc,))
            deleted["bets"] = cur.rowcount or 0
            cur.execute("DELETE FROM rounds WHERE open_at < %s;", (start_utc,))
            deleted["rounds"] = cur.rowcount or 0

    return {"ok": True, "deleted": deleted}
