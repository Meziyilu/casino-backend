# app.py
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
import os
import psycopg
from passlib.context import CryptContext
import jwt  # PyJWT

APP_NAME = "casino-backend"

# ================= FastAPI & CORS =================
app = FastAPI(title=APP_NAME)

# 上線請保留實際前端網域；開發可暫時加入 localhost
ALLOWED_ORIGINS = [
    "https://casino-frontend-pya7.onrender.com",
    "https://topz0705.com",
    "https://www.topz0705.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,   # 若測試不通，可暫改為 ["*"] 再收斂
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= DB DDL =================
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
  side TEXT,        -- 'player' | 'banker' | 'tie'
  amount BIGINT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

def init_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        print("[init_db] DATABASE_URL is not set; skip migrations.")
        return

    # DDL 建議 autocommit
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 建表
            cur.execute(DDL_USERS)
            cur.execute(DDL_ROUNDS)
            cur.execute(DDL_BETS)

            # 追加欄位（⚠ 不能寫 ALTER TABLE IF NOT EXISTS）
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;")

            # 唯一索引（允許多個 NULL，不擋未設定者）
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username
                ON users(username);
            """)

    print("[init_db] ensured tables/columns: users(username,password_hash), rounds, bets")

@app.on_event("startup")
def on_startup():
    init_db()

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

# 取得目前登入者（Authorization: Bearer <token>）
def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split(" ", 1)[1]
    data = parse_token(token)
    return {"user_id": int(data["sub"]), "username": data["username"]}

# 管理員白名單（環境變數 ADMIN_USERS=admin,topz0705）
def get_admin_usernames() -> set[str]:
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

# ================= Basic Routes =================
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
    return {"id": user["user_id"], "username": user["username"]}

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

# ================= Admin (JWT + 白名單) =================
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
