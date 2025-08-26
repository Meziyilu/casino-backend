# baccarat/service.py
import os, asyncio, pytz, psycopg
from datetime import datetime, timezone

from .logic import deal_round

TZ = pytz.timezone("Asia/Taipei")

ROOMS = {
    "room1": 30,   # 30 秒
    "room2": 60,   # 60 秒
    "room3": 90,   # 90 秒
}

def _conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(dsn, autocommit=True)

def today_key() -> str:
    return datetime.now(timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")

def _midnight_changed(last_day: str | None) -> bool:
    return (last_day is not None) and (last_day != today_key())

def get_last_round_no(conn: psycopg.Connection, room: str, day_key: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT round_no FROM rounds WHERE room = %s AND day_key = %s ORDER BY round_no DESC LIMIT 1;",
            (room, day_key),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

def open_round(conn, room, day_key, round_no):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rounds (room, day_key, round_no, state, opened_at)
            VALUES (%s, %s, %s, 'betting', NOW())
            ON CONFLICT (room, day_key, round_no) DO NOTHING;
            """,
            (room, day_key, round_no),
        )

def lock_round(conn, room, day_key, round_no):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE rounds
            SET state='locked', locked_at=NOW()
            WHERE room=%s AND day_key=%s AND round_no=%s;
            """,
            (room, day_key, round_no),
        )

def settle_round(conn, room, day_key, round_no):
    # 發牌並結算
    deal = deal_round()
    outcome = deal["outcome"]
    pt = deal["player_total"]
    bt = deal["banker_total"]

    with conn.cursor() as cur:
        # 更新局結果
        cur.execute(
            """
            UPDATE rounds
            SET state='settled',
                settled_at=NOW(),
                player_cards=%s,
                banker_cards=%s,
                player_total=%s,
                banker_total=%s,
                outcome=%s
            WHERE room=%s AND day_key=%s AND round_no=%s;
            """,
            (
                deal["player_cards"],
                deal["banker_cards"],
                pt, bt, outcome,
                room, day_key, round_no
            ),
        )

        # 派彩：莊0.95、閒1、和8；遇和局時莊/閒退回
        cur.execute(
            "SELECT user_id, side, amount FROM bets WHERE room=%s AND day_key=%s AND round_no=%s;",
            (room, day_key, round_no),
        )
        for user_id, side, amount in cur.fetchall():
            payout = 0
            refund = 0
            if outcome == "tie":
                if side in ("player","banker"):
                    refund = amount  # 退回
                elif side == "tie":
                    payout = amount * 8
            else:
                if side == outcome:
                    if side == "player":
                        payout = amount * 1
                    elif side == "banker":
                        payout = int(amount * 0.95)
                    # side == tie 不會在這裡
            delta = payout + refund
            if delta:
                cur.execute("UPDATE users SET balance = balance + %s WHERE id=%s;", (delta, user_id))

async def dealer_loop(room: str, seconds_per_round: int):
    conn = _conn()
    try:
        dk = today_key()
        current_no = get_last_round_no(conn, room, dk)

        while True:
            # 跨日重置
            if _midnight_changed(dk):
                dk = today_key()
                current_no = 0

            # 開新局
            current_no += 1
            open_round(conn, room, dk, current_no)

            # 下注時段
            lock_after = max(1, int(seconds_per_round * 0.7))
            await asyncio.sleep(lock_after)

            # 鎖單
            lock_round(conn, room, dk, current_no)

            # 發牌結算
            await asyncio.sleep(max(1, seconds_per_round - lock_after))
            try:
                settle_round(conn, room, dk, current_no)
            except Exception as e:
                print(f"[DEALER][{room}] settle error:", e)

            # 緩衝
            await asyncio.sleep(1)
    except Exception as e:
        print(f"[DEALER][{room}] loop crashed:", e)
    finally:
        conn.close()

async def launch_all_rooms():
    tasks = []
    for r, sec in ROOMS.items():
        tasks.append(asyncio.create_task(dealer_loop(r, sec)))
    await asyncio.gather(*tasks)
