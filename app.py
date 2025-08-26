import os
import psycopg
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import datetime

APP_NAME = "Casino Auth + Lobby (Reset Clean)"
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL")

# ---------- DB ----------
@contextmanager
def get_conn():
    with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
        yield conn

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

# ---------- APP & CORS ----------
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

# ---------- Models ----------
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

# ---------- Auth ----------
@app.post("/auth/register")
def register(body: RegisterReq):
    un = body.username.strip().lower()
    pw = body.password.strip()
    if not un or not pw:
        raise HTTPException(status_code=400, detail="username/password required")
    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (username, password, nickname) VALUES (%s, %s, %s) RETURNING id;",
                    (un, pw, un),
                )
                uid = cur.fetchone()[0]
                conn.commit()
                return {"ok": True, "id": uid, "username": un}
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                raise HTTPException(status_code=409, detail="username already exists")

@app.post("/auth/login")
def login(body: LoginReq):
    un = body.username.strip().lower()
    pw = body.password.strip()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, password FROM users WHERE username=%s;", (un,))
            row = cur.fetchone()
            if not row or row[1] != pw:
                raise HTTPException(status_code=401, detail="invalid username or password")
            uid = row[0]
    return {"ok": True, "id": uid, "username": un, "token": f"user-{uid}"}

@app.get("/me", response_model=UserOut)
def me(username: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              SELECT id, username, nickname, balance, is_admin, created_at
              FROM users WHERE username=%s;
            """, (username,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="user not found")
    return UserOut(
        id=row[0], username=row[1], nickname=row[2],
        balance=int(row[3]), is_admin=bool(row[4]), created_at=row[5]
    )

@app.get("/health")
def health():
    return {"ok": True, "name": APP_NAME, "allowed": get_allowed_origins()}

# ---------- Admin: 一鍵清庫重建 ----------
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

def require_admin(authorization: str = Header(default="")):
    parts = authorization.split(" ", 1)
    supplied = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    if not ADMIN_TOKEN or supplied != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")

@app.post("/admin/reset-db")
def admin_reset_db(_=Depends(require_admin)):
    """
    ⚠️ 不可逆：刪除 baccarat 相關表與 users 表，重建乾淨 users 表。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 刪 baccarat 相關（存在才刪）
            cur.execute("DROP TABLE IF EXISTS bets CASCADE;")
            cur.execute("DROP TABLE IF EXISTS rounds CASCADE;")
            # 刪 users 並重建
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
    return {"ok": True, "message": "database reset to clean users-only schema"}

@app.on_event("startup")
def on_startup():
    init_db()
