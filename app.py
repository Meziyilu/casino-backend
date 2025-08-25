# app.py
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from typing import Optional, Set
import os
import psycopg
from passlib.context import CryptContext
import jwt  # PyJWT

APP_NAME = "casino-backend"

# ================= FastAPI & CORS =================
app = FastAPI(title=APP_NAME)

ALLOWED_ORIGINS = [
    "https://casino-frontend-pya7.onrender.com",
    "https://topz0705.com",
    "https://www.topz0705.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,   # 若測試不通可暫改 ["*"] 再收斂
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= DB DDL（基礎表） =================
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
  outcome TEXT  -- 'player' | 'banker' | 'tie'
);
"""

DDL_BETS = """
CREATE TABLE IF NOT EXISTS bets (
  id BIGSERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  round_no INT NOT NULL,
  side TEXT,        -- 'player' | 'banker' | 'tie' | pair 類
  amount BIGINT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

def init_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        print("[init_db] DATABASE_URL is not set; skip migrations.")
        return
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 建表
            cur.execute(DDL_USERS)
            cur.execute(DDL_ROUNDS)
            cur.execute(DDL_BETS)
            # users 追加欄位
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;")
            cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);""")
    print("[init_db] ensured tables/columns: users(username,password_hash), rounds, bets")

def ensure_schema_ext():
    url = os.getenv("DATABASE_URL")
    if not url:
        return
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # rounds 擴充：狀態與下注截止
            cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open'")
            cur.execute("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS betting_deadline TIMESTAMPTZ")
            # bets 擴充：多項目投注
            cur.execute("ALTER TABLE bets ADD COLUMN IF NOT EXISTS bet_type TEXT DEFAULT 'main'")
            # 索引
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rounds_round_no ON rounds(round_no)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_rounds_status ON rounds(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_round_no ON bets(round_no)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_user_round ON bets(user_id, round_no)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bets_composite ON bets(round_no, bet_type)")
    print("[ensure_schema_ext] done")

@app.on_event("startup")
def on_startup():
    init_db()
    ensure_schema_ext()

# ================= Security (password & JWT) =================
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
JWT_ALG = "HS256"
JWT_EXP_MIN = 60 * 24 * 7  # 7 天
SECRET = os.getenv("SECRET_KEY", "dev-secret")  # Render 設定 SECRET_KEY

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
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split(" ", 1)[1]
    data = parse_token(token)
    return {"user_id": int(data["sub"]), "username": data["username"]}

def get_admin_usernames() -> Set[str]:
    raw = os.getenv("ADMIN_USERS", "")
    return set(u.strip() for u in raw.split(",") if u.strip())

# ================= Schemas =================
class RegisterIn(BaseModel):
    username: str
    password: str
    nickname: str | None = None

class LoginIn(BaseModel):
    username: str
    password: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

class GrantIn(BaseModel):
    username: str
    amount: int  # 正數加、負數扣

# ================= Basic & Health =================
@app.get("/")
def root():
    return {"message": f"{APP_NAME} running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/db-check")
def db_check():
    url = os.getenv("DATABASE_URL")
    if not url:
        return {"ok": False, "reason": "DATABASE_URL missing"}
    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                one = cur.fetchone()[0]
        return {"ok": one == 1}
    except Exception as e:
        return {"ok": False, "reason": str(e)}

# ================= Auth =================
@app.post("/auth/register", response_model=TokenOut)
def register(body: RegisterIn):
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(500, "DATABASE_URL not set")
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s", (body.username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Username already exists")
            cur.execute(
                "INSERT INTO users (username, password_hash, nickname) VALUES (%s,%s,%s) RETURNING id",
                (body.username, hash_pw(body.password), body.nickname),
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
                raise HTTPException(status_code=401, detail="Invalid credentials")
            user_id = row[0]
    return TokenOut(access_token=make_token(user_id, body.username))

@app.get("/me")
def me(user=Depends(get_current_user)):
    admins = get_admin_usernames()
    return {"id": user["user_id"], "username": user["username"], "is_admin": user["username"] in admins}

# ================= Balance =================
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

# ================= Admin: Grant (白名單) =================
@app.post("/admin/grant")
def admin_grant(body: GrantIn, user=Depends(get_current_user)):
    admins = get_admin_usernames()
    if user["username"] not in admins:
        raise HTTPException(403, "Forbidden: not admin")
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

# ================= Helpers: Rounds =================
def get_open_round(cur):
    cur.execute("""
        SELECT id, round_no, opened_at, player_total, banker_total, outcome, status, betting_deadline
        FROM rounds
        WHERE status='open' AND outcome IS NULL
        ORDER BY round_no DESC
        LIMIT 1
    """)
    return cur.fetchone()

def next_round_no(cur) -> int:
    cur.execute("SELECT COALESCE(MAX(round_no), 0) FROM rounds")
    return int(cur.fetchone()[0] or 0) + 1

# ================= 桌況/倒數 =================
@app.get("/rounds/current")
def rounds_current():
    url = os.getenv("DATABASE_URL")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            r = get_open_round(cur)
            now = datetime.now(timezone.utc)
            if not r:
                return {"round_no": None, "status": "idle", "server_time": now.isoformat(), "betting_deadline": None, "remain_sec": 0}
            _, round_no, opened_at, pt, bt, outcome, status, ddl = r
            remain = int(max(0, (ddl - now).total_seconds())) if ddl else None
            return {
                "round_no": round_no,
                "status": status,
                "betting_deadline": ddl.isoformat() if ddl else None,
                "server_time": now.isoformat(),
                "remain_sec": remain,
            }

# ================= Admin: 開/關 注 =================
class OpenIn(BaseModel):
    duration_sec: int = 20  # 本局下注時間（秒）

@app.post("/admin/open-round")
def admin_open_round(body: OpenIn, user=Depends(get_current_user)):
    admins = get_admin_usernames()
    if user["username"] not in admins:
        raise HTTPException(403, "Forbidden: not admin")
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
        raise HTTPException(403, "Forbidden: not admin")
    url = os.getenv("DATABASE_URL")
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            r = get_open_round(cur)
            if not r:
                raise HTTPException(400, "No open round")
            rid, round_no, *_ = r
            cur.execute("UPDATE rounds SET status='closed' WHERE id=%s", (rid,))
            return {"ok": True, "round_no": round_no}

# ================= 下註（含倒數/關單/多類型） =================
class BetIn(BaseModel):
    side: str          # 'player'|'banker'|'tie'|'player_pair'|'banker_pair'|'any_pair'|'perfect_pair'
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
            rid, round_no, opened_at, pt, bt, outcome, status, ddl = r
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

# ================= 歷史 =================
@app.get("/rounds/last10")
def last10():
    url = os.getenv("DATABASE_URL")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT round_no, opened_at, player_total, banker_total, outcome
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
        })
    return {"rows": data}

# ================= 結算（真實賠率 + 對子） =================
class SettleIn(BaseModel):
    round_no: Optional[int] = None
    player_total: int
    banker_total: int
    outcome: str  # 'player'|'banker'|'tie'
    player_pair: bool = False
    banker_pair: bool = False
    any_pair: bool = False
    perfect_pair: bool = False

MAIN_ODDS = {"player": 1.0, "banker": 0.95, "tie": 8.0}
PAIR_ODDS = {
    "player_pair": 11.0,
    "banker_pair": 11.0,
    "any_pair": 5.0,
    "perfect_pair": 25.0,
}

@app.post("/admin/settle-round")
def admin_settle_round(body: SettleIn, user=Depends(get_current_user)):
    admins = get_admin_usernames()
    if user["username"] not in admins:
        raise HTTPException(403, "Forbidden: not admin")

    outcome = body.outcome.lower()
    if outcome not in ("player","banker","tie"):
        raise HTTPException(400, "invalid outcome")

    url = os.getenv("DATABASE_URL")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            # 找要結算的 round
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

            # 更新 round 結果與關閉
            cur.execute("""
                UPDATE rounds
                SET player_total=%s, banker_total=%s, outcome=%s, status='closed'
                WHERE id=%s
            """, (body.player_total, body.banker_total, outcome, rid))

            # 讀該局所有下注
            cur.execute("""
                SELECT user_id, side, amount, bet_type
                FROM bets
                WHERE round_no=%s
            """, (round_no,))
            rows = cur.fetchall()

            pay_map = {}  # user_id -> to_credit
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
                        if side == "tie":
                            mult = 1 + MAIN_ODDS["tie"]    # 本金 + 8x
                        else:
                            mult = 1.0                      # push：退本金
                    else:
                        if side == outcome:
                            mult = 1 + MAIN_ODDS[outcome]  # 本金 + 賠率
                        else:
                            mult = 0.0
                else:
                    # 對子類：贏才給 本金 + 賠率
                    if pair_result.get(bet_type, False):
                        mult = 1 + PAIR_ODDS[bet_type]

                if mult > 0:
                    credit(uid, amount * mult)

            # 入帳
            for uid, add in pay_map.items():
                cur.execute("UPDATE users SET balance = COALESCE(balance,0) + %s WHERE id=%s", (add, uid))

        conn.commit()

    return {"ok": True, "round_no": round_no, "outcome": outcome, "payout_done": True}
