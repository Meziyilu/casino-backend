# backend/app.py — Final (60s下注、揭牌等完再開新局、/my_bets、CORS)
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
import random

# --------- Models ---------
class Card(BaseModel):
    rank: str
    suit: str

class DealResult(BaseModel):
    player_cards: List[Card]
    banker_cards: List[Card]
    player_total: int
    banker_total: int
    outcome: str
    player_pair: bool
    banker_pair: bool
    any_pair: bool
    perfect_pair: bool
    used_no_commission: bool

class PlaceBet(BaseModel):
    user_id: str
    round: int
    bets: Dict[str, int]

class UserResult(BaseModel):
    user_id: str
    round: int
    payout: int
    balance: int

# --------- App ---------
app = FastAPI(title="Baccarat Backend — Final")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------- Globals ---------
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
CARD_VALUES = {**{str(i): i for i in range(2, 10)}, "A": 1, "10": 0, "J": 0, "Q": 0, "K": 0}

round_counter = 1
round_start = datetime.now(timezone.utc)
round_bets: Dict[str, Dict[str, int]] = {}
user_balances: Dict[str, int] = {}
round_results: Dict[int, DealResult] = {}
user_results: List[UserResult] = []

# --------- Helpers ---------
def draw_card():
    return Card(rank=random.choice(RANKS), suit=random.choice(SUITS))

def hand_value(cards: List[Card]) -> int:
    return sum(CARD_VALUES[c.rank] for c in cards) % 10

def evaluate_baccarat():
    player = [draw_card(), draw_card()]
    banker = [draw_card(), draw_card()]
    player_total = hand_value(player)
    banker_total = hand_value(banker)

    outcome = (
        "player" if player_total > banker_total
        else "banker" if banker_total > player_total
        else "tie"
    )

    player_pair = (player[0].rank == player[1].rank)
    banker_pair = (banker[0].rank == banker[1].rank)
    any_pair = player_pair or banker_pair
    perfect_pair = player_pair and banker_pair

    return DealResult(
        player_cards=player,
        banker_cards=banker,
        player_total=player_total,
        banker_total=banker_total,
        outcome=outcome,
        player_pair=player_pair,
        banker_pair=banker_pair,
        any_pair=any_pair,
        perfect_pair=perfect_pair,
        used_no_commission=False
    )

def settle_round(r: int, result: DealResult):
    global user_balances, user_results
    if r not in round_bets:
        return

    for uid, bets in round_bets[r].items():
        if uid not in user_balances:
            user_balances[uid] = 1000
        payout = 0
        for bet_type, amount in bets.items():
            if bet_type == result.outcome:
                if bet_type == "tie":
                    payout += amount * 8
                else:
                    payout += amount * 2
            elif bet_type == "player_pair" and result.player_pair:
                payout += amount * 11
            elif bet_type == "banker_pair" and result.banker_pair:
                payout += amount * 11
        user_balances[uid] += payout - sum(bets.values())
        user_results.append(UserResult(
            user_id=uid, round=r, payout=payout, balance=user_balances[uid]
        ))

# --------- Background Round ---------
def new_round():
    global round_counter, round_start, round_results
    result = evaluate_baccarat()
    round_results[round_counter] = result
    settle_round(round_counter, result)
    round_counter += 1
    round_start = datetime.now(timezone.utc)

# --------- API ---------
@app.get("/status")
def status():
    elapsed = (datetime.now(timezone.utc) - round_start).total_seconds()
    remaining = max(0, 60 - elapsed)
    return {
        "round": round_counter,
        "round_start": round_start.isoformat(),
        "remaining_seconds": remaining,
        "last_result": round_results.get(round_counter - 1)
    }

@app.post("/bet")
def place_bet(bet: PlaceBet):
    if bet.round != round_counter:
        raise HTTPException(400, "Invalid round.")
    if bet.user_id not in user_balances:
        user_balances[bet.user_id] = 1000
    if sum(bet.bets.values()) > user_balances[bet.user_id]:
        raise HTTPException(400, "Insufficient balance.")

    if round_counter not in round_bets:
        round_bets[round_counter] = {}
    round_bets[round_counter][bet.user_id] = bet.bets
    return {"status": "accepted"}

@app.get("/my_bets/{user_id}")
def my_bets(user_id: str):
    bets = {r: bets[user_id] for r, bets in round_bets.items() if user_id in bets}
    results = [res for res in user_results if res.user_id == user_id]
    return {"bets": bets, "results": results}

@app.get("/results/{round_id}")
def get_result(round_id: int):
    return round_results.get(round_id)

@app.on_event("startup")
async def scheduler():
    from asyncio import sleep
    async def loop():
        global round_start
        while True:
            elapsed = (datetime.now(timezone.utc) - round_start).total_seconds()
            if elapsed >= 60:
                new_round()
            await sleep(1)
    import asyncio
    asyncio.create_task(loop())
# === 加在檔案頂部 if 沒有的話 ===
from fastapi import Header, Depends
from pydantic import BaseModel
from typing import Optional

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

# === 小工具：驗證管理權限 ===
def require_admin(authorization: Optional[str] = Header(None)):
    token = (authorization or "").split(" ", 1)
    supplied = token[1] if len(token) == 2 and token[0].lower() == "bearer" else (authorization or "")
    if not ADMIN_TOKEN or supplied != ADMIN_TOKEN:
        # 也允許用 query ?token=... 傳；可自行移除
        # from fastapi import Request
        # if request.query_params.get("token") == ADMIN_TOKEN: return
        raise HTTPException(status_code=401, detail="unauthorized")

# === 執行多條 SQL 的工具 ===
def run_sql_list(cur, stmts: list[str]):
    results = []
    for s in stmts:
        if not s.strip():
            continue
        cur.execute(s)
        results.append(s.split("\n", 1)[0].strip())
    return results

# === 請求模型 ===
class FixRoundsReq(BaseModel):
    cleanup: Optional[str] = "none"  # "none" | "today" | "all"

# === 管理 API：一鍵修復 rounds 索引/約束 +（可選）清理資料 ===
@app.post("/admin/fix-rounds")
def admin_fix_rounds(body: FixRoundsReq, _=Depends(require_admin)):
    """
    修復內容：
    1) 刪除只鎖 round_no 的唯一索引/約束（避免跨房/跨日衝突）
    2) 補上 day_key（台北時區日期）與缺失的 room 值
    3) 建立正確的複合唯一索引 (room, day_key, round_no)
    4) （可選）清理舊資料：cleanup=none/today/all
    """
    executed = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1) 刪舊索引（log 顯示為 idx_rounds_round_no）
            stmts = [
                "DROP INDEX IF EXISTS idx_rounds_round_no;",
                # 若當初被建立成 UNIQUE CONSTRAINT（名字不一定），掃描並移除任何含 round_no 的唯一約束
                """
                DO $$
                DECLARE
                  cons_name text;
                BEGIN
                  SELECT conname INTO cons_name
                  FROM pg_constraint
                  WHERE conrelid = 'rounds'::regclass
                    AND contype = 'u'
                    AND conname ILIKE '%round_no%';
                  IF cons_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE rounds DROP CONSTRAINT %I', cons_name);
                  END IF;
                END $$;
                """,
                # 2) 補 day_key（以台北時間的日期），補缺失 room
                """
                UPDATE rounds
                   SET day_key = (opened_at AT TIME ZONE 'Asia/Taipei')::date
                 WHERE day_key IS NULL;
                """,
                "UPDATE rounds SET room = 'room1' WHERE room IS NULL;",
                "UPDATE bets   SET room = 'room1' WHERE room IS NULL;",
                # 3) 建立正確複合唯一索引
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uniq_round_room_day_no
                  ON rounds (room, day_key, round_no);
                """
            ]
            executed += run_sql_list(cur, stmts)

            # 4) 可選清理
            if body.cleanup == "all":
                executed += run_sql_list(cur, [
                    "TRUNCATE bets RESTART IDENTITY CASCADE;",
                    "TRUNCATE rounds RESTART IDENTITY CASCADE;"
                ])
            elif body.cleanup == "today":
                executed += run_sql_list(cur, [
                    """
                    DELETE FROM bets
                     WHERE created_at::date < (now() AT TIME ZONE 'Asia/Taipei')::date;
                    """,
                    """
                    DELETE FROM rounds
                     WHERE day_key < (now() AT TIME ZONE 'Asia/Taipei')::date;
                    """
                ])

        conn.commit()

    return {
        "ok": True,
        "cleanup": body.cleanup,
        "executed": executed
    }
