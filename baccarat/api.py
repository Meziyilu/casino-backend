# baccarat/api.py
from fastapi import APIRouter, HTTPException, Header, Request
from datetime import datetime
import pytz
from typing import Dict
from .sql import db, ensure_schema
from .schema import PlaceBetReq, StateResp, HistoryItem, RevealResp

router = APIRouter(prefix="/baccarat", tags=["baccarat"])
TZ = pytz.timezone("Asia/Taipei")

@router.on_event("startup")
def _boot():
    ensure_schema()

@router.get("/rooms")
def list_rooms():
    # 固定三房
    return [
        {"id":"room1","title":"百家樂 #1","seconds":30},
        {"id":"room2","title":"百家樂 #2","seconds":60},
        {"id":"room3","title":"百家樂 #3","seconds":90},
    ]

def _current_round_no(room:str, day) -> int:
    with db() as c:
        cur = c.cursor()
        cur.execute("""
          SELECT round_no FROM rounds
          WHERE room=%s AND day_key=%s
          ORDER BY round_no DESC LIMIT 1
        """,(room, day))
        row = cur.fetchone()
        return row[0] if row else 1

@router.get("/state", response_model=StateResp)
def state(room:str):
    day = datetime.now(TZ).date()
    with db() as c:
        cur = c.cursor()
        cur.execute("""
          SELECT round_no, opened_at, locked, settled
          FROM rounds
          WHERE room=%s AND day_key=%s
          ORDER BY round_no DESC LIMIT 1
        """, (room, day))
        row = cur.fetchone()

        if not row:
            # 尚未開局，回預設
            return StateResp(
                room=room, day_key=str(day), round_no=1,
                status="betting", seconds_left=0, totals={"player":0,"banker":0,"tie":0}, bettors=0
            )

        rno, opened_at, locked, settled = row
        # 統計
        cur.execute("""
          SELECT side, COALESCE(SUM(amount),0) FROM bets
          WHERE room=%s AND day_key=%s AND round_no=%s
          GROUP BY side
        """,(room, day, rno))
        totals: Dict[str,int] = {"player":0,"banker":0,"tie":0}
        for s, amt in cur.fetchall():
            totals[s] = int(amt)

        cur.execute("""
          SELECT COUNT(DISTINCT user_id) FROM bets
          WHERE room=%s AND day_key=%s AND round_no=%s
        """,(room, day, rno))
        bettors = cur.fetchone()[0]

        # 狀態/倒數
        now = datetime.now(TZ)
        from .service import ROOMS_CFG, REVEAL_SECONDS
        cycle = ROOMS_CFG.get(room, 60)
        if not locked and not settled:
            # 下注期
            left = max(0, cycle - int((now - opened_at).total_seconds()))
            status = "betting"
        elif locked and not settled:
            # 開牌動畫期
            # left 顯示動畫剩餘秒
            # 取鎖單到現在的秒數
            elapsed = int((now - opened_at).total_seconds())
            left = max(0, REVEAL_SECONDS - max(0, elapsed - cycle))
            status = "dealing"
        else:
            status = "settled"
            left = 0

        return StateResp(
            room=room, day_key=str(day), round_no=rno,
            status=status, seconds_left=left, totals=totals, bettors=bettors
        )

@router.get("/reveal", response_model=RevealResp)
def reveal(room:str):
    """回傳最新一局的開牌結果（給動畫用）"""
    with db() as c:
        cur = c.cursor()
        cur.execute("""
          SELECT player_cards, banker_cards, player_total, banker_total, winner, settled
          FROM rounds
          WHERE room=%s AND day_key=%s
          ORDER BY round_no DESC LIMIT 1
        """, (room, datetime.now(TZ).date()))
        row = cur.fetchone()
        if not row:
            return RevealResp(show=False, winner=None)
        pc, bc, pt, bt, w, _ = row
        return RevealResp(
            show=True, winner=w,
            player_cards=(pc.split(",") if pc else []),
            banker_cards=(bc.split(",") if bc else []),
            player_total=pt, banker_total=bt
        )

@router.get("/history", response_model=list[HistoryItem])
def history(room:str, limit:int=10):
    with db() as c:
        cur = c.cursor()
        cur.execute("""
          SELECT round_no, winner, player_total, banker_total
          FROM rounds
          WHERE room=%s AND day_key=%s AND settled=TRUE
          ORDER BY round_no DESC LIMIT %s
        """, (room, datetime.now(TZ).date(), limit))
        return [
            HistoryItem(round_no=r, winner=w, pt=pt, bt=bt)
            for (r,w,pt,bt) in cur.fetchall()
        ]

@router.post("/bet")
def place_bet(req: PlaceBetReq, x_user_id:int=Header(None)):
    if req.side not in ("player","banker","tie") or req.amount <= 0:
        raise HTTPException(422, "invalid bet")
    if not x_user_id:
        # 你也可以改成從 session cookie 解析 user_id
        raise HTTPException(401, "Missing X-User-Id")

    day = datetime.now(TZ).date()
    with db() as c:
        cur = c.cursor()
        # 找這局
        cur.execute("""
          SELECT round_no, locked FROM rounds
          WHERE room=%s AND day_key=%s
          ORDER BY round_no DESC LIMIT 1
        """,(req.room, day))
        row = cur.fetchone()
        if not row:
            raise HTTPException(409, "round not ready")
        rno, locked = row
        if locked:
            raise HTTPException(409, "betting closed")

        # 下注（此版採「下注時不先扣」；若要先扣，這裡 UPDATE users SET balance=balance-amount）
        cur.execute("""
          INSERT INTO bets(user_id, room, day_key, round_no, side, amount)
          VALUES (%s,%s,%s,%s,%s,%s)
        """,(x_user_id, req.room, day, rno, req.side, req.amount))

    return {"ok": True, "room": req.room, "round_no": rno}
