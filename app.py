# app.py
import os
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import psycopg
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from passlib.hash import bcrypt

APP_NAME = "Casino API"

# ---------- env ----------
DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme")
TZ = timezone(timedelta(hours=8))  # Asia/Taipei

def get_allowed_origins() -> List[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "https://topz0705.com",
        "https://casino-frontend-pya7.onrender.com",
        "http://localhost:5173",
    ]

# ---------- app ----------
app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- DB ----------
def db():
    return psycopg.connect(DATABASE_URL, autocommit=True)

def init_db():
    with db() as conn, conn.cursor() as cur:
        # users
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
          id SERIAL PRIMARY KEY,
          username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          nickname TEXT,
          balance BIGINT DEFAULT 0,
          created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)
        # index
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);")
init_db()

# ---------- models ----------
class AuthIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=40)
    password: str = Field(..., min_length=6, max_length=64)
    nickname: Optional[str] = None

class UserOut(BaseModel):
    id: int
    username: str
    nickname: Optional[str]
    balance: int
    created_at: datetime

def row_user_to_out(r) -> UserOut:
    return UserOut(id=r[0], username=r[1], nickname=r[3], balance=r[4], created_at=r[5])

# ---------- helpers ----------
def hash_pw(p: str) -> str:
    return bcrypt.hash(p)

def verify_pw(p: str, h: str) -> bool:
    try:
        return bcrypt.verify(p, h)
    except Exception:
        return False

# ---------- Auth ----------
@app.post("/auth/register")
def register(payload: AuthIn):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE username=%s", (payload.username,))
        if cur.fetchone():
            raise HTTPException(409, "USERNAME_TAKEN")
        cur.execute(
            "INSERT INTO users(username,password_hash,nickname,balance) VALUES(%s,%s,%s,0) RETURNING id,username,password_hash,nickname,balance,created_at",
            (payload.username, hash_pw(payload.password), payload.nickname or payload.username),
        )
        r = cur.fetchone()
        return {"ok": True, "user": row_user_to_out(r).model_dump()}

@app.post("/auth/login")
def login(payload: AuthIn):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id,username,password_hash,nickname,balance,created_at FROM users WHERE username=%s", (payload.username,))
        r = cur.fetchone()
        if not r or not verify_pw(payload.password, r[2]):
            raise HTTPException(401, "INVALID_CREDENTIALS")
        return {"ok": True, "user": row_user_to_out(r).model_dump()}

# ---------- Lobby ----------
@app.get("/lobby/summary")
def lobby_summary():
    now = datetime.now(TZ)
    start = datetime(now.year, now.month, now.day, tzinfo=TZ)
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COALESCE(SUM(balance),0) FROM users")
        total_users, total_balance = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM users WHERE created_at >= %s", (start.astimezone(timezone.utc),))
        today_users = cur.fetchone()[0]
    return {"ok": True, "summary": {
        "totalUsers": total_users, "todayNewUsers": today_users, "totalBalance": int(total_balance)
    }}

# =========================================================
#                     Admin API (token)
# =========================================================
def require_admin(x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "ADMIN_TOKEN_INVALID")
    return True

class QueryUsersIn(BaseModel):
    q: Optional[str] = ""
    page: int = 1
    size: int = 20

class GrantIn(BaseModel):
    amount: int = Field(..., ge=-10_000_000, le=10_000_000)

class SetBalanceIn(BaseModel):
    balance: int = Field(..., ge=0, le=10_000_000_000)

class ResetPwIn(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=64)

@app.get("/admin/stats")
def admin_stats(_: bool = Depends(require_admin)):
    now = datetime.now(TZ)
    start = datetime(now.year, now.month, now.day, tzinfo=TZ)
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COALESCE(SUM(balance),0) FROM users")
        total_users, total_balance = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM users WHERE created_at >= %s", (start.astimezone(timezone.utc),))
        today_users = cur.fetchone()[0]
    return {"ok": True, "stats": {
        "totalUsers": total_users, "todayNewUsers": today_users, "totalBalance": int(total_balance)
    }}

@app.post("/admin/users")
def admin_users(body: QueryUsersIn, _: bool = Depends(require_admin)):
    q = (body.q or "").strip()
    page = max(1, body.page)
    size = min(100, max(1, body.size))
    offset = (page - 1) * size

    with db() as conn, conn.cursor() as cur:
        if q:
            like = f"%{q}%"
            cur.execute("""
                SELECT id,username,password_hash,nickname,balance,created_at
                FROM users
                WHERE username ILIKE %s OR nickname ILIKE %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, (like, like, size, offset))
        else:
            cur.execute("""
                SELECT id,username,password_hash,nickname,balance,created_at
                FROM users
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, (size, offset))
        rows = cur.fetchall()
        users = [row_user_to_out(r).model_dump() for r in rows]

        # total
        if q:
            cur.execute("SELECT COUNT(*) FROM users WHERE username ILIKE %s OR nickname ILIKE %s", (like, like))
        else:
            cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]

    return {"ok": True, "total": total, "page": page, "size": size, "items": users}

@app.post("/admin/users/{uid}/grant")
def admin_grant(uid: int, body: GrantIn, _: bool = Depends(require_admin)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET balance = balance + %s WHERE id=%s RETURNING id,username,password_hash,nickname,balance,created_at",
                    (body.amount, uid))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "USER_NOT_FOUND")
    return {"ok": True, "user": row_user_to_out(r).model_dump()}

@app.post("/admin/users/{uid}/set-balance")
def admin_set_balance(uid: int, body: SetBalanceIn, _: bool = Depends(require_admin)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET balance=%s WHERE id=%s RETURNING id,username,password_hash,nickname,balance,created_at",
                    (body.balance, uid))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "USER_NOT_FOUND")
    return {"ok": True, "user": row_user_to_out(r).model_dump()}

@app.post("/admin/users/{uid}/reset-password")
def admin_reset_password(uid: int, body: ResetPwIn, _: bool = Depends(require_admin)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET password_hash=%s WHERE id=%s RETURNING id,username,password_hash,nickname,balance,created_at",
                    (hash_pw(body.new_password), uid))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "USER_NOT_FOUND")
    return {"ok": True}

@app.delete("/admin/users/{uid}")
def admin_delete_user(uid: int, _: bool = Depends(require_admin)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id=%s RETURNING 1", (uid,))
        if not cur.fetchone():
            raise HTTPException(404, "USER_NOT_FOUND")
    return {"ok": True}

# health
@app.get("/")
def root():
    return {"ok": True, "service": APP_NAME}
