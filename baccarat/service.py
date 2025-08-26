# baccarat/service.py
import asyncio, random
from datetime import datetime, timedelta
import pytz
from typing import List, Tuple, Optional
from .sql import db

TZ = pytz.timezone("Asia/Taipei")

# 三房週期秒數
ROOMS_CFG = {
    "room1": 30,
    "room2": 60,
    "room3": 90,
}

REVEAL_SECONDS = 7  # 開牌動畫時間（API 會回 "dealing" 讓前端顯示動畫）

# 牌面與點數
RANKS = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"]
def card_value(rank: str) -> int:
    if rank == "A": return 1
    if rank in ["10","J","Q","K"]: return 0
    return int(rank)

def hand_total(cards: List[str]) -> int:
    return sum(card_value(r) for r in cards) % 10

def draw_card() -> str:
    return random.choice(RANKS)

def deal_baccarat() -> Tuple[List[str], List[str], int, int]:
    """回傳 (player_cards, banker_cards, pt, bt)，遵循真實補牌規則"""
    p = [draw_card(), draw_card()]
    b = [draw_card(), draw_card()]
    pt = hand_total(p); bt = hand_total(b)

    # Natural 停牌
    if pt in (8,9) or bt in (8,9):
        return p, b, pt, bt

    # Player 規則
    player_third: Optional[str] = None
    if pt <= 5:
        player_third = draw_card()
        p.append(player_third)
        pt = hand_total(p)

    # Banker 規則
    if player_third is None:
        # Player 沒補牌：Banker <=5 補，>=6 停
        if bt <= 5:
            b.append(draw_card())
            bt = hand_total(b)
    else:
        t = bt
        x = card_value(player_third)
        # 依標準表
        if t <= 2:
            b.append(draw_card())
            bt = hand_total(b)
        elif t == 3 and x != 8:
            b.append(draw_card()); bt = hand_total(b)
        elif t == 4 and x in [2,3,4,5,6,7]:
            b.append(draw_card()); bt = hand_total(b)
        elif t == 5 and x in [4,5,6,7]:
            b.append(draw_card()); bt = hand_total(b)
        elif t == 6 and x in [6,7]:
            b.append(draw_card()); bt = hand_total(b)
        # else 停

    return p, b, pt, bt

def compute_winner(pt:int, bt:int) -> str:
    if pt > bt: return "player"
    if bt > pt: return "banker"
    return "tie"

async def dealer_loop(room: str, cycle_sec: int):
    """每房間自動：下注期(未鎖單) → 鎖單/開牌(dealing) → settled → 下一局。每日 00:00 重置 round_no"""
    while True:
        try:
            today = datetime.now(TZ).date()
            with db() as c:
                cur = c.cursor()
                # 決定這局 round_no（跨日重置）
                cur.execute("""
                  SELECT round_no, day_key FROM rounds
                  WHERE room=%s ORDER BY day_key DESC, round_no DESC LIMIT 1
                """, (room,))
                row = cur.fetchone()
                next_no = 1 if (not row or row[1] != today) else row[0] + 1

                # 開盤（可下注）
                cur.execute("""
                  INSERT INTO rounds(room, day_key, round_no, locked, settled)
                  VALUES (%s,%s,%s,FALSE,FALSE)
                  ON CONFLICT(room, day_key, round_no) DO NOTHING
                """, (room, today, next_no))
        except Exception as e:
            print(f"[DEALER][{room}] open error:", e)

        # 下注時間
        await asyncio.sleep(cycle_sec)

        # 鎖單/開牌
        try:
            today = datetime.now(TZ).date()
            with db() as c:
                cur = c.cursor()
                cur.execute("""
                  UPDATE rounds SET locked=TRUE
                  WHERE room=%s AND day_key=%s AND settled=FALSE
                  ORDER BY round_no DESC LIMIT 1
                """, (room, today))

                # 發牌+計算
                p, b, pt, bt = deal_baccarat()
                winner = compute_winner(pt, bt)

                # 更新牌與點數、暫不 settled 先進入 dealing 讓前端播動畫
                cur.execute("""
                  UPDATE rounds
                  SET player_cards=%s, banker_cards=%s,
                      player_total=%s, banker_total=%s, winner=%s
                  WHERE room=%s AND day_key=%s AND round_no=
                    (SELECT MAX(round_no) FROM rounds WHERE room=%s AND day_key=%s)
                """, (",".join(p), ",".join(b), pt, bt, winner, room, today, room, today))
        except Exception as e:
            print(f"[DEALER][{room}] deal error:", e)

        # 開牌動畫期間
        await asyncio.sleep(REVEAL_SECONDS)

        # 結算派彩
        try:
            with db() as c:
                cur = c.cursor()
                # 取這局
                cur.execute("""
                  SELECT round_no, winner FROM rounds
                  WHERE room=%s AND day_key=%s
                  ORDER BY round_no DESC LIMIT 1
                """, (room, datetime.now(TZ).date()))
                r = cur.fetchone()
                if not r: 
                    continue
                rno, winner = r

                # 取這局所有下注
                cur.execute("""
                  SELECT user_id, side, amount FROM bets
                  WHERE room=%s AND day_key=%s AND round_no=%s
                """, (room, datetime.now(TZ).date(), rno))
                rows = cur.fetchall()

                # 結算：player 1:1，banker 0.95，tie 8:1；若結果 tie，押 P/B 退回本金
                for uid, side, amt in rows:
                    delta = 0
                    if winner == "tie":
                        if side == "tie":
                            delta = amt * 8
                        else:
                            delta = 0           # 退本金 → 不在這裡做；我們用「不扣款 + 下注先不扣」或「下注先扣，tie 再加回」，二選一
                    else:
                        if side == winner:
                            if winner == "player": delta = amt * 1
                            elif winner == "banker": delta = int(amt * 0.95)
                        else:
                            delta = -amt

                        # 若 tie 且押 P/B：退回本金（改為加回）
                    if winner == "tie" and side in ("player","banker"):
                        delta = 0  # 押注不變動（下注時不先扣）

                    if delta != 0:
                        cur.execute("UPDATE users SET balance = balance + %s WHERE id=%s", (delta, uid))

                # 標記 settled
                cur.execute("""
                  UPDATE rounds SET settled=TRUE
                  WHERE room=%s AND day_key=%s AND round_no=%s
                """, (room, datetime.now(TZ).date(), rno))
        except Exception as e:
            print(f"[DEALER][{room}] settle error:", e)

        # 下一局前緩衝
        await asyncio.sleep(2)

async def launch_all_rooms():
    await asyncio.gather(*[
        dealer_loop(room, sec) for room, sec in ROOMS_CFG.items()
    ])
