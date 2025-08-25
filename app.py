# app.py
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from typing import Optional, Set
import os
import asyncio
import random
import psycopg
from passlib.context import CryptContext
import jwt  # PyJWT

APP_NAME = "casino-backend"

app = FastAPI(title=APP_NAME)

# ======== CORS ========
ALLOWED_ORIGINS = [
    "*",  # 你也可以收斂為：前端正式網域，例如 https://topz0705.com
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======== Auto dealer config ========
AUTO_DEAL = os.getenv("AUTO_DEAL", "1") == "1"
AUTO_BET_SEC = int(os.getenv("AUTO_BET_SEC", "60"))
AUTO_REVEAL_SEC = int(os.getenv("AUTO_REVEAL_SEC", "15"))
AUTO_GAP_SEC = int(os.getenv("AUTO_GAP_SEC", "3"))

# ======== DB schema ========
DDL_USERS = """
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  tg_id TEXT UNIQUE,
  nickname TEXT,
  balance BIGINT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);
"""
DDL_ROUNDS = """
CREATE TABLE IF NOT EXISTS rounds (
  id BIGSERIAL PRIMARY KEY,
  round_no INT NOT NULL,
  opened_at TIMESTAMPTZ DEFAULT now(),
  player_total INT,
  banker_total INT,
  outcome TEXT
);
"""
DDL_BETS = """
CREATE TABLE IF NOT EXISTS bets (
  id BIGSERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  round_no INT NOT NULL,
  side TEXT,
  amount BIGINT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

def init_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        print("[init_db] DATABASE_URL not set; skip.")
        return
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL_USERS)
            cur.execute(DDL_ROUNDS)
            cur.execute(DDL_BETS)
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);")
    print("[init_db] base tables ensured")

def ensure_schema_ext():
    url = os.getenv("DATABASE_URL")
    if not url:
        return
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # rounds：狀態、下注截止、補牌旗標
            cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open'")
            cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS betting_deadline TIMESTAMPTZ")
            cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS player_draw3 BOOLEAN")
            cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS banker_draw3 BOOLEAN")
            # bets：副注
            cur.execute("ALTER TABLE bets ADD COLUMN IF NOT EXISTS bet_type TEXT DEFAULT 'main'")
            # 索引
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rounds_round_no ON rounds(round_no)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_rounds_status ON rounds(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_round_no ON bets(round_no)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_user_round ON bets(user_id, round_no)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_composite ON bets(round_no, bet_type)")
    print("[ensure_schema_ext] done")

# ======== Security (password & JWT) ========
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
JWT_ALG = "HS256"
JWT_EXP_MIN = 60 * 24 * 7  # 7 days
SECRET = os.getenv("SECRET_KEY", "dev-secret")

def hash_pw(p: str) -> str:
    return pwd_ctx.hash(p)

def verify_pw(p: str, h: str) -> bool:
    try:
        return pwd_ctx.verify(p, h or "")
    except Exception:
        return False

def make_token(user_id: int, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXP_MIN)).timestamp()),
    }
    return jwt.encode(payload, SECRET, algorithm=JWT_ALG)

def parse_token(token: str):
    try:
        return jwt.decode(token, SECRET, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")

def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing Authorization header")
    token = authorization.split(" ", 1)[1]
    data = parse_token(token)
    return {"user_id": int(data["sub"]), "username": data["username"]}

def get_admin_usernames() -> Set[str]:
    raw = os.getenv("ADMIN_USERS", "")
    return set(u.strip() for u in raw.split(",") if u.strip())

# ======== Schemas ========
class RegisterIn(BaseModel):
    username: str
    password: str
    nickname: Optional[str] = None

class LoginIn(BaseModel):
    username: str
    password: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

class GrantIn(BaseModel):
    username: str
    amount: int

# ======== Health ========
@app.get("/")
def root():
    return {"message": f"{APP_NAME} running"}

@app.get("/health")
def health():
    return {"status": "ok"}

# ======== Auth ========
@app.post("/auth/register", response_model=TokenOut)
def register(body: RegisterIn):
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(500, "DATABASE_URL not set")
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s", (body.username,))
            if cur.fetchone():
                raise HTTPException(409, "Username already exists")
            cur.execute(
                "INSERT INTO users (username, password_hash, nickname, balance) VALUES (%s,%s,%s, %s) RETURNING id",
                (body.username, hash_pw(body.password), body.nickname, 10000),
            )
            user_id = cur.fetchone()[0]
    return TokenOut(access_token=make_token(user_id, body.username))

@app.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn):
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(500, "DATABASE_URL not set")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, password_hash FROM users WHERE username=%s", (body.username,))
            row = cur.fetchone()
            if not row or not verify_pw(body.password, row[1]):
                raise HTTPException(401, "Invalid credentials")
            user_id = row[0]
    return TokenOut(access_token=make_token(user_id, body.username))

@app.get("/me")
def me(user=Depends(get_current_user)):
    admins = get_admin_usernames()
    return {"id": user["user_id"], "username": user["username"], "is_admin": user["username"] in admins}

# ======== Balance ========
@app.get("/balance")
def get_balance(user=Depends(get_current_user)):
    url = os.getenv("DATABASE_URL")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM users WHERE id=%s", (user["user_id"],))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "User not found")
            return {"balance": int(row[0] or 0)}

# ======== Admin: grant ========
@app.post("/admin/grant")
def admin_grant(body: GrantIn, user=Depends(get_current_user)):
    admins = get_admin_usernames()
    if user["username"] not in admins:
        raise HTTPException(403, "Forbidden")
    if body.amount == 0:
        raise HTTPException(400, "amount cannot be 0")
    url = os.getenv("DATABASE_URL")
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET balance = COALESCE(balance,0) + %s WHERE username=%s RETURNING balance",
                (body.amount, body.username),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "User not found")
            return {"username": body.username, "balance": int(row[0])}

# ======== Helpers: rounds ========
def get_open_round(cur):
    cur.execute("""
        SELECT id, round_no, opened_at, player_total, banker_total, outcome, status, betting_deadline,
               COALESCE(player_draw3,false), COALESCE(banker_draw3,false)
        FROM rounds
        WHERE status='open' AND outcome IS NULL
        ORDER BY round_no DESC
        LIMIT 1
    """)
    return cur.fetchone()

def next_round_no(cur) -> int:
    cur.execute("SELECT COALESCE(MAX(round_no), 0) FROM rounds")
    return int(cur.fetchone()[0] or 0) + 1

# ======== 桌況 / 倒數 ========
@app.get("/rounds/current")
def rounds_current():
    url = os.getenv("DATABASE_URL")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            r = get_open_round(cur)
            now = datetime.now(timezone.utc)
            if not r:
                # 也可能剛結束在 reveal 期間；仍回 closed 狀態
                cur.execute("""
                    SELECT round_no, status, betting_deadline FROM rounds
                    WHERE outcome IS NOT NULL
                    ORDER BY round_no DESC LIMIT 1
                """)
                last = cur.fetchone()
                if last:
                    return {"round_no": last[0], "status": "closed", "server_time": now.isoformat(), "betting_deadline": None, "remain_sec": 0}
                return {"round_no": None, "status": "idle", "server_time": now.isoformat(), "betting_deadline": None, "remain_sec": 0}
            _, round_no, opened_at, pt, bt, outcome, status, ddl, p3, b3 = r
            remain = int(max(0, (ddl - now).total_seconds())) if ddl else None
            return {
                "round_no": round_no,
                "status": status,
                "betting_deadline": ddl.isoformat() if ddl else None,
                "server_time": now.isoformat(),
                "remain_sec": remain,
            }

# ======== Admin: 開/關 注 ========
class OpenIn(BaseModel):
    duration_sec: int = 20

@app.post("/admin/open-round")
def admin_open_round(body: OpenIn, user=Depends(get_current_user)):
    admins = get_admin_usernames()
    if user["username"] not in admins:
        raise HTTPException(403, "Forbidden")
    url = os.getenv("DATABASE_URL")
    now = datetime.now(timezone.utc)
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            if get_open_round(cur):
                raise HTTPException(400, "There is already an open round")
            rn = next_round_no(cur)
            ddl = now + timedelta(seconds=max(5, body.duration_sec))
            cur.execute(
                "INSERT INTO rounds (round_no, status, betting_deadline) VALUES (%s,'open',%s) RETURNING id",
                (rn, ddl)
            )
            rid = cur.fetchone()[0]
            return {"round_id": rid, "round_no": rn, "betting_deadline": ddl.isoformat()}

@app.post("/admin/close-round")
def admin_close_round(user=Depends(get_current_user)):
    admins = get_admin_usernames()
    if user["username"] not in admins:
        raise HTTPException(403, "Forbidden")
    url = os.getenv("DATABASE_URL")
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            r = get_open_round(cur)
            if not r:
                raise HTTPException(400, "No open round")
            rid, round_no, *_ = r
            cur.execute("UPDATE rounds SET status='closed' WHERE id=%s", (rid,))
            return {"ok": True, "round_no": round_no}

# ======== Bet（主注/副注） ========
class BetIn(BaseModel):
    side: str   # 'player'|'banker'|'tie'|'player_pair'|'banker_pair'|'any_pair'|'perfect_pair'
    amount: int

ALLOWED_SIDES = {"player","banker","tie","player_pair","banker_pair","any_pair","perfect_pair"}

@app.post("/bet")
def place_bet(body: BetIn, user=Depends(get_current_user)):
    side = body.side.lower()
    if side not in ALLOWED_SIDES:
        raise HTTPException(400, f"side must be one of {sorted(ALLOWED_SIDES)}")
    if body.amount <= 0:
        raise HTTPException(400, "amount must be > 0")

    url = os.getenv("DATABASE_URL")
    now = datetime.now(timezone.utc)
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            r = get_open_round(cur)
            if not r:
                raise HTTPException(400, "No open round to bet on")
            rid, round_no, opened_at, pt, bt, outcome, status, ddl, p3, b3 = r
            if status != "open":
                raise HTTPException(400, "Betting closed")
            if ddl and now >= ddl:
                raise HTTPException(400, "Betting time over")

            # 扣款
            cur.execute("SELECT balance FROM users WHERE id=%s FOR UPDATE", (user["user_id"],))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "User not found")
            bal = int(row[0] or 0)
            if bal < body.amount:
                raise HTTPException(400, "Insufficient balance")
            cur.execute("UPDATE users SET balance=%s WHERE id=%s", (bal - body.amount, user["user_id"]))

            # 紀錄下注
            bet_type = "main" if side in ("player","banker","tie") else side
            cur.execute(
                "INSERT INTO bets (user_id, round_no, side, amount, bet_type) VALUES (%s,%s,%s,%s,%s)",
                (user["user_id"], round_no, side, body.amount, bet_type)
            )
        conn.commit()
    return {"ok": True, "round_no": round_no}

# ======== 歷史（含補牌旗標） ========
@app.get("/rounds/last10")
def last10():
    url = os.getenv("DATABASE_URL")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT round_no, opened_at, player_total, banker_total, outcome,
                       COALESCE(player_draw3,false), COALESCE(banker_draw3,false)
                FROM rounds
                ORDER BY round_no DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
    data = []
    for r in rows:
        data.append({
            "round_no": r[0],
            "opened_at": r[1].isoformat() if r[1] else None,
            "player_total": r[2],
            "banker_total": r[3],
            "outcome": r[4],
            "player_draw3": bool(r[5]),
            "banker_draw3": bool(r[6]),
        })
    return {"rows": data}

# ======== 結算（真實賠率 + 對子） ========
class SettleIn(BaseModel):
    round_no: Optional[int] = None
    player_total: int
    banker_total: int
    outcome: str  # 'player'|'banker'|'tie'
    player_pair: bool = False
    banker_pair: bool = False
    any_pair: bool = False
    perfect_pair: bool = False
    player_draw3: Optional[bool] = None
    banker_draw3: Optional[bool] = None

MAIN_ODDS = {"player": 1.0, "banker": 0.95, "tie": 8.0}
PAIR_ODDS = {"player_pair": 11.0, "banker_pair": 11.0, "any_pair": 5.0, "perfect_pair": 25.0}

@app.post("/admin/settle-round")
def admin_settle_round(body: SettleIn, user=Depends(get_current_user)):
    admins = get_admin_usernames()
    if user["username"] not in admins:
        raise HTTPException(403, "Forbidden")

    outcome = body.outcome.lower()
    if outcome not in ("player","banker","tie"):
        raise HTTPException(400, "invalid outcome")
    url = os.getenv("DATABASE_URL")

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            # 找 round
            if body.round_no is None:
                r = get_open_round(cur)
                if not r:
                    raise HTTPException(400, "No open round")
                rid, round_no, *_ = r
            else:
                round_no = body.round_no
                cur.execute("SELECT id, outcome FROM rounds WHERE round_no=%s", (round_no,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(404, "Round not found")
                rid, out = row
                if out is not None:
                    raise HTTPException(400, "Round already settled")

            p3 = body.player_draw3 if body.player_draw3 is not None else (body.player_total <= 5)
            b3 = body.banker_draw3 if body.banker_draw3 is not None else (body.banker_total <= 5)

            # 更新 round 結果
            cur.execute("""
                UPDATE rounds
                SET player_total=%s, banker_total=%s, outcome=%s,
                    status='closed', player_draw3=%s, banker_draw3=%s
                WHERE id=%s
            """, (body.player_total, body.banker_total, outcome, p3, b3, rid))

            # 下注
            cur.execute("SELECT user_id, side, amount, bet_type FROM bets WHERE round_no=%s", (round_no,))
            rows = cur.fetchall()

            pay_map = {}
            def credit(uid, amt):
                pay_map[uid] = pay_map.get(uid, 0) + int(amt)

            pair_result = {
                "player_pair": body.player_pair,
                "banker_pair": body.banker_pair,
                "any_pair": body.any_pair or body.player_pair or body.banker_pair,
                "perfect_pair": body.perfect_pair,
            }

            for uid, side, amount, bet_type in rows:
                mult = 0.0
                if bet_type == "main":
                    if outcome == "tie":
                        mult = 1 + MAIN_ODDS["tie"] if side == "tie" else 1.0
                    else:
                        mult = 1 + MAIN_ODDS[outcome] if side == outcome else 0.0
                else:
                    if pair_result.get(bet_type, False):
                        mult = 1 + PAIR_ODDS[bet_type]
                if mult > 0:
                    credit(uid, amount * mult)

            for uid, add in pay_map.items():
                cur.execute("UPDATE users SET balance = COALESCE(balance,0) + %s WHERE id=%s", (add, uid))
        conn.commit()

    return {"ok": True, "round_no": round_no, "outcome": outcome, "payout_done": True}

# ======== Auto dealer（背景） ========
LOCK_KEY = 987654321
def try_take_lock(cur) -> bool:
    cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
    return bool(cur.fetchone()[0])
def release_lock(cur):
    cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))

def random_outcome():
    r = random.random()
    if r < 0.46: return "banker"
    if r < 0.90: return "player"
    return "tie"
def random_totals(outcome: str):
    if outcome == "tie":
        pt = bt = random.randint(0, 9)
    elif outcome == "player":
        pt = random.randint(5, 9); bt = random.randint(0, 7)
    else:
        bt = random.randint(5, 9); pt = random.randint(0, 7)
    return pt % 10, bt % 10
def random_pairs():
    return {
        "player_pair": random.random() < 0.08,
        "banker_pair": random.random() < 0.08,
        "any_pair": False,
        "perfect_pair": random.random() < 0.01,
    }

async def auto_dealer_loop():
    await asyncio.sleep(2)
    url = os.getenv("DATABASE_URL")
    if not url:
        return
    while True:
        try:
            # 取得鎖 & 修正任何逾時的 open 局
            with psycopg.connect(url, autocommit=True) as conn:
                with conn.cursor() as cur:
                    if not try_take_lock(cur):
                        await asyncio.sleep(2); continue

                    cur.execute("""
                        SELECT id, round_no, status, betting_deadline
                        FROM rounds
                        WHERE outcome IS NULL
                        ORDER BY round_no DESC LIMIT 1
                    """)
                    row = cur.fetchone()
                    now = datetime.now(timezone.utc)

                    if row and row[2]=='open' and row[3] and now >= row[3]:
                        # 補關單 + 結算
                        cur.execute("UPDATE rounds SET status='closed' WHERE id=%s", (row[0],))
                        outcome = random_outcome()
                        pt, bt = random_totals(outcome)
                        p3, b3 = (pt <= 5), (bt <= 5)
                        pairs = random_pairs()
                        cur.execute("""
                            UPDATE rounds SET player_total=%s, banker_total=%s, outcome=%s,
                                player_draw3=%s, banker_draw3=%s
                            WHERE id=%s
                        """, (pt, bt, outcome, p3, b3, row[0]))
                        # 派彩
                        cur.execute("SELECT user_id, side, amount, bet_type FROM bets WHERE round_no=%s", (row[1],))
                        bets = cur.fetchall()
                        pay_map = {}
                        def credit(uid, amt):
                            pay_map[uid] = pay_map.get(uid, 0) + int(amt)
                        pair_result = {
                            "player_pair": pairs["player_pair"],
                            "banker_pair": pairs["banker_pair"],
                            "any_pair": pairs["player_pair"] or pairs["banker_pair"],
                            "perfect_pair": pairs["perfect_pair"],
                        }
                        for uid, side, amount, bet_type in bets:
                            mult = 0.0
                            if bet_type == "main":
                                if outcome == "tie":
                                    mult = 1 + MAIN_ODDS["tie"] if side == "tie" else 1.0
                                else:
                                    mult = 1 + MAIN_ODDS[outcome] if side == outcome else 0.0
                            else:
                                if pair_result.get(bet_type, False):
                                    mult = 1 + PAIR_ODDS[bet_type]
                            if mult > 0:
                                credit(uid, amount * mult)
                        for uid, add in pay_map.items():
                            cur.execute("UPDATE users SET balance = COALESCE(balance,0) + %s WHERE id=%s", (add, uid))

                    # 沒 open 局就立即開新局
                    cur.execute("SELECT 1 FROM rounds WHERE status='open' AND outcome IS NULL LIMIT 1")
                    has_open = cur.fetchone() is not None
                    if not has_open:
                        rn = next_round_no(cur)
                        ddl = datetime.now(timezone.utc) + timedelta(seconds=max(5, AUTO_BET_SEC))
                        cur.execute(
                            "INSERT INTO rounds (round_no, status, betting_deadline) VALUES (%s,'open',%s)",
                            (rn, ddl)
                        )
                    release_lock(cur)

            # 正常流程：等待截止→關單→結算→等待動畫→緩衝
            with psycopg.connect(url, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, round_no, betting_deadline FROM rounds
                        WHERE status='open' AND outcome IS NULL
                        ORDER BY round_no DESC LIMIT 1
                    """)
                    row = cur.fetchone()
                    if row:
                        rid, round_no, ddl = row
                        sleep_sec = max(0, int((ddl - datetime.now(timezone.utc)).total_seconds()))
                        if sleep_sec > 0: await asyncio.sleep(sleep_sec)

                        # 關單
                        cur.execute("UPDATE rounds SET status='closed' WHERE id=%s", (rid,))

                        # 結算
                        outcome = random_outcome()
                        pt, bt = random_totals(outcome)
                        p3, b3 = (pt <= 5), (bt <= 5)
                        pairs = random_pairs()
                        cur.execute("""
                            UPDATE rounds SET player_total=%s, banker_total=%s, outcome=%s,
                                player_draw3=%s, banker_draw3=%s
                            WHERE id=%s
                        """, (pt, bt, outcome, p3, b3, rid))

                        # 派彩
                        cur.execute("SELECT user_id, side, amount, bet_type FROM bets WHERE round_no=%s", (round_no,))
                        bets = cur.fetchall()
                        pay_map = {}
                        def credit(uid, amt):
                            pay_map[uid] = pay_map.get(uid, 0) + int(amt)
                        pair_result = {
                            "player_pair": pairs["player_pair"],
                            "banker_pair": pairs["banker_pair"],
                            "any_pair": pairs["player_pair"] or pairs["banker_pair"],
                            "perfect_pair": pairs["perfect_pair"],
                        }
                        for uid, side, amount, bet_type in bets:
                            mult = 0.0
                            if bet_type == "main":
                                if outcome == "tie":
                                    mult = 1 + MAIN_ODDS["tie"] if side == "tie" else 1.0
                                else:
                                    mult = 1 + MAIN_ODDS[outcome] if side == outcome else 0.0
                            else:
                                if pair_result.get(bet_type, False):
                                    mult = 1 + PAIR_ODDS[bet_type]
                            if mult > 0:
                                credit(uid, amount * mult)
                        for uid, add in pay_map.items():
                            cur.execute("UPDATE users SET balance = COALESCE(balance,0) + %s WHERE id=%s", (add, uid))

                        # 等待前端動畫
                        await asyncio.sleep(AUTO_REVEAL_SEC)
                        await asyncio.sleep(AUTO_GAP_SEC)

            await asyncio.sleep(1)
        except Exception as e:
            print("[auto_dealer_loop] error:", e)
            await asyncio.sleep(3)

# ======== Startup ========
@app.on_event("startup")
def on_startup():
    init_db()
    ensure_schema_ext()
    if AUTO_DEAL:
        asyncio.create_task(auto_dealer_loop())
        print(f"[auto] enabled: bet={AUTO_BET_SEC}s reveal={AUTO_REVEAL_SEC}s gap={AUTO_GAP_SEC}s")
