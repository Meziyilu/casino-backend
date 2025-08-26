# baccarat/service.py
import asyncio, os, random
from datetime import timedelta
from util.db import db
from .sql import today_key, taipei_now, ensure_schema, BETTING_SECONDS, ROOMS, current_round_info, next_round_no, room_pools

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin-dev")

# 簡單的房態快取（倒數截止時間、當前 round_no、phase）
_room_state: dict[str, dict] = {
    # room: { "round_no": int, "phase": "betting|reveal|settled", "deadline": datetime }
}

def get_state_snapshot(room: str):
    s = _room_state.get(room, {})
    now = taipei_now()
    seconds_left = None
    if s.get("deadline"):
        seconds_left = max(0, int((s["deadline"] - now).total_seconds()))
    return {
        "room": room,
        "round_no": s.get("round_no"),
        "phase": s.get("phase"),
        "seconds_left": seconds_left,
    }

# --- 百家樂點數與補牌規則（10/J/Q/K 都算 0，A=1，2~9 其面值；總點數取 %10） ---
def draw_card():
    # 牌面 1..13 -> 牌點 1..9 or 0
    v = random.randint(1, 13)
    return 1 if v == 1 else (0 if v >= 10 else v)

def total(cards):
    return sum(cards) % 10

def compute_baccarat_result():
    # 依賭場標準第三張牌規則（簡化：等同常見表）
    p = [draw_card(), draw_card()]
    b = [draw_card(), draw_card()]
    pt = total(p)
    bt = total(b)

    # natural
    if pt in (8, 9) or bt in (8, 9):
        return p, b, pt, bt, False, False

    # 閒先補
    p3_flag = False
    if pt <= 5:
        p.append(draw_card())
        p3_flag = True
        pt = total(p)

    # 莊補牌規則
    b3_flag = False
    if not p3_flag:
        if bt <= 5:
            b.append(draw_card())
            b3_flag = True
            bt = total(b)
    else:
        third = p[2]
        # 根據表格（常見規則）
        draw = False
        if bt <= 2:
            draw = True
        elif bt == 3 and third != 8:
            draw = True
        elif bt == 4 and (2 <= third <= 7):
            draw = True
        elif bt == 5 and (4 <= third <= 7):
            draw = True
        elif bt == 6 and (6 <= third <= 7):
            draw = True
        if draw:
            b.append(draw_card())
            b3_flag = True
            bt = total(b)

    return p, b, pt, bt, p3_flag, b3_flag

async def single_room_loop(room: str):
    # 使用 advisory lock 確保單實例
    lock_key = 0xBACC00 + hash(room) % 10000
    while True:
        try:
            ensure_schema()
            with db() as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s);", (lock_key,))
                got = cur.fetchone()["pg_try_advisory_lock"]
                if not got:
                    # 其他實例在跑
                    await asyncio.sleep(2)
                    continue

                # 開新局（若需要）
                info = current_round_info(cur, room)
                if info is None or info["phase"] == "settled":
                    rn = next_round_no(cur, room)
                    cur.execute("""
                      INSERT INTO rounds (day_key, room, round_no, phase)
                      VALUES (%s, %s, %s, 'betting')
                      ON CONFLICT (day_key, room, round_no) DO NOTHING;
                    """, (today_key(), room, rn))
                    conn.commit()
                    info = current_round_info(cur, room)

                # 更新房態
                rn = info["round_no"]
                _room_state[room] = {
                    "round_no": rn,
                    "phase": "betting",
                    "deadline": taipei_now() + timedelta(seconds=BETTING_SECONDS[room]),
                }
            # betting 倒數
            await asyncio.sleep(BETTING_SECONDS[room])

            # 鎖單 -> 進入 reveal
            with db() as conn, conn.cursor() as cur:
                cur.execute("""
                  UPDATE rounds
                  SET phase='reveal'
                  WHERE day_key=%s AND room=%s AND round_no=%s;
                """, (today_key(), room, rn))
                conn.commit()
                _room_state[room]["phase"] = "reveal"

            # 計算結果
            p, b, pt, bt, p3, b3 = compute_baccarat_result()
            outcome = "player" if pt > bt else ("banker" if bt > pt else "tie")

            # 儲存結果
            with db() as conn, conn.cursor() as cur:
                cur.execute("""
                  UPDATE rounds
                  SET player_total=%s, banker_total=%s,
                      player_draw3=%s, banker_draw3=%s,
                      outcome=%s
                  WHERE day_key=%s AND room=%s AND round_no=%s;
                """, (pt, bt, p3, b3, outcome, today_key(), room, rn))
                conn.commit()

            # 給前端一段揭示時間
            await asyncio.sleep(5)

            # 結算（含和局 push）
            with db() as conn, conn.cursor() as cur:
                # 撈池子
                pools, _ = room_pools(cur, room, rn)
                # 結算每位下注者
                # player 1:1, banker 0.95, tie 8:1；tie 時 player/banker 退回本金
                cur.execute("""
                  SELECT b.id, b.user_id, b.side, b.amount
                  FROM bets b
                  WHERE b.day_key=%s AND b.room=%s AND b.round_no=%s;
                """, (today_key(), room, rn))
                rows = cur.fetchall()
                for r in rows:
                    uid, side, amt = r["user_id"], r["side"], int(r["amount"])
                    win = 0
                    if outcome == "player":
                        if side == "player": win = amt * 2
                    elif outcome == "banker":
                        if side == "banker": win = amt + int(amt * 0.95)
                    else:  # tie
                        if side == "tie": win = amt + amt * 8
                        else: win = amt  # push

                    if win > 0:
                        cur.execute("UPDATE users SET balance = balance + %s WHERE id=%s;", (win, uid))
                # 設定 settled
                cur.execute("""
                  UPDATE rounds SET phase='settled'
                  WHERE day_key=%s AND room=%s AND round_no=%s;
                """, (today_key(), room, rn))
                conn.commit()

            # 稍等 2 秒，再開下一局
            await asyncio.sleep(2)

        except Exception as e:
            # 記錄錯誤但不中斷循環
            print(f"[DEALER][{room}] error: {e}")
            await asyncio.sleep(2)

async def launch_all_rooms():
    # 每天 00:00(台北) 自動換日（靠 day_key），round_no 從 1 開始（由 next_round_no 計算）
    await asyncio.gather(*(single_room_loop(r) for r in ROOMS))

def current_room_state(room: str):
    snap = get_state_snapshot(room)
    # 附上池子統計
    with db() as conn, conn.cursor() as cur:
        rn = snap.get("round_no")
        pools = {"player": 0, "banker": 0, "tie": 0}
        bettors = 0
        if rn:
            pools, bettors = room_pools(cur, room, rn)
        # 如果 phase=reveal/settled 取結果摘要
        result = None
        cur.execute("""
          SELECT player_total, banker_total, player_draw3, banker_draw3, outcome
          FROM rounds
          WHERE day_key=%s AND room=%s AND round_no=%s;
        """, (today_key(), room, rn or 0))
        row = cur.fetchone()
        if row and row["outcome"]:
            result = {
                "player_total": row["player_total"],
                "banker_total": row["banker_total"],
                "player_draw3": row["player_draw3"],
                "banker_draw3": row["banker_draw3"],
                "winner": row["outcome"],
            }
    snap["pools"] = pools
    snap["bettors"] = bettors
    snap["result"] = result
    return snap
