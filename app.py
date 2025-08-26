import os
import psycopg
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import datetime

APP_NAME = "Casino Auth + Lobby"
DATABASE_URL = os.getenv("DATABASE_URL")

# ===== DB 連線 =====
@contextmanager
def get_conn():
    with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
        yield conn

# ===== 初始化資料庫 =====
def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
              id SERIAL PRIMARY KEY,
              username TEXT UNIQUE,
              password TEXT,
              nickname TEXT,
              balance BIGINT DEFAULT 1000,
              is_admin BOOLEAN DEFAULT FALSE,
              created_at TIMESTAMPTZ DEFAULT now()
            );
            """)
        conn.commit()

# ===== FastAPI app =====
app = FastAPI(title=APP_NAME)

def get_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "https://topz0705.com",
        "https://casino-frontend-pya7.onrender.com",
        "http://localhost:5173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== Pydantic models =====
class RegisterReq(BaseModel):
    username: str
    password: str

class LoginReq(BaseModel):
    username: str
    password: str

class UserOut(BaseModel):
    id: int
    username: str
    nickname: Optional[str]
    balance: int
    is_admin: bool
    created_at: datetime.datetime

# ===== Auth APIs =====
@app.post("/auth/register")
def register(body: RegisterReq):
    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (username, password) VALUES (%s, %s) RETURNING id;",
                    (body.username, body.password),
                )
                user_id = cur.fetchone()[0]
            except psycopg.errors.UniqueViolation:
                raise HTTPException(status_code=409, detail="username already exists")
        conn.commit()
    return {"ok": True, "id": user_id}

@app.post("/auth/login")
def login(body: LoginReq):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, password FROM users WHERE username=%s;", (body.username,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=401, detail="invalid username or password")
            if row[1] != body.password:
                raise HTTPException(status_code=401, detail="invalid username or password")
    return {"ok": True, "id": row[0], "username": body.username}

@app.get("/me", response_model=UserOut)
def me(username: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, nickname, balance, is_admin, created_at FROM users WHERE username=%s;", (username,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="user not found")
    return UserOut(
        id=row[0], username=row[1], nickname=row[2],
        balance=row[3], is_admin=row[4], created_at=row[5]
    )

# ===== Admin API: Reset DB =====
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

def require_admin(authorization: str = Header(default="")):
    parts = authorization.split(" ", 1)
    supplied = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    if not ADMIN_TOKEN or supplied != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")

@app.post("/admin/reset-db")
def admin_reset_db(_=Depends(require_admin)):
    """
    ⚠️ 危險操作：清空舊資料 & 重建最小結構（users）。
    會 DROP baccarat 表（rounds/bets），然後重建 users。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1) 刪掉 baccarat 表
            cur.execute("DROP TABLE IF EXISTS bets CASCADE;")
            cur.execute("DROP TABLE IF EXISTS rounds CASCADE;")
            # 2) 重建 users
            cur.execute("DROP TABLE IF EXISTS users CASCADE;")
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
              id SERIAL PRIMARY KEY,
              username TEXT UNIQUE,
              password TEXT,
              nickname TEXT,
              balance BIGINT DEFAULT 1000,
              is_admin BOOLEAN DEFAULT FALSE,
              created_at TIMESTAMPTZ DEFAULT now()
            );
            """)
        conn.commit()
    return {"ok": True, "message": "database reset to auth-only schema"}

# ===== 啟動時初始化 =====
@app.on_event("startup")
def on_startup():
    init_db()
