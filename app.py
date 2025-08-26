# app.py  — Auth + Lobby (minimal)
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import os, psycopg

APP_NAME = os.getenv("APP_NAME", "Casino Backend - Auth Only")
DATABASE_URL = os.environ["DATABASE_URL"]  # 必填：Render 上的 External Database URL
ADMIN_USERS: set[str] = {
    x.strip().lower() for x in os.getenv("ADMIN_USERS", "").split(",") if x.strip()
}

# ------------------------------------------------------------------------------
# CORS 設定：優先讀取 ALLOWED_ORIGINS，沒設就用安全的預設清單
# ------------------------------------------------------------------------------
def get_allowed_origins() -> List[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "https://topz0705.com",                          # 你的正式網域
        "https://casino-frontend-pya7.onrender.com",     # Render 前端域名
        "http://localhost:5173",                         # 本機開發
    ]

app = FastAPI(title=APP_NAME)

# 很重要：一定要加在路由註冊之前
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------------------
# DB helpers
# ------------------------------------------------------------------------------
def get_conn():
    return psycopg.connect(DATABASE_URL, autocommit=False)

def init_db():
    """只建立 users 表，不動其他表，保留既有資料。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
              id         SERIAL PRIMARY KEY,
              username   TEXT UNIQUE,
              password   TEXT,
              nickname   TEXT,
              balance    BIGINT DEFAULT 1000,
              is_admin   BOOLEAN DEFAULT FALSE,
              created_at TIMESTAMPTZ DEFAULT now()
            );
            """)
        conn.commit()

init_db()

# ------------------------------------------------------------------------------
# Models
# ------------------------------------------------------------------------------
class RegisterReq(BaseModel):
    username: str
    password: str
    nickname: Optional[str] = None

class LoginReq(BaseModel):
    username: str
    password: str

# ------------------------------------------------------------------------------
# Token helpers
# ------------------------------------------------------------------------------
def issue_token(user_id: int) -> str:
    return f"user-{user_id}"

def parse_token(token: Optional[str]) -> Optional[int]:
    if not token:
        return None
    if token.startswith("user-"):
        try:
            return int(token.split("-", 1)[1])
        except Exception:
            return None
    return None

def extract_token(req: Request) -> Optional[str]:
    q = req.query_params.get("token")
    if q:
        return q
    auth = req.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1]
    return None

# ------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "ok": True,
        "name": APP_NAME,
        "time": datetime.now(timezone.utc).isoformat(),
        "allowed_origins": get_allowed_origins(),
    }

@app.post("/auth/register")
def register(req: RegisterReq):
    un = req.username.strip().lower()
    pw = req.password.strip()
    nick = (req.nickname or "").strip() or un
    if not un or not pw:
        raise HTTPException(status_code=400, detail="帳號與密碼不可為空")

    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                is_admin = un in ADMIN_USERS
                cur.execute("""
                  INSERT INTO users (username, password, nickname, is_admin)
                  VALUES (%s, %s, %s, %s)
                  RETURNING id, balance, is_admin
                """, (un, pw, nick, is_admin))
                uid, bal, is_admin_val = cur.fetchone()
            conn.commit()
            return {
                "id": uid,
                "username": un,
                "nickname": nick,
                "balance": int(bal),
                "is_admin": bool(is_admin_val),
            }
        except psycopg.Error:
            conn.rollback()
            raise HTTPException(status_code=409, detail="使用者已存在")

@app.post("/auth/login")
def login(req: LoginReq):
    un = req.username.strip().lower()
    pw = req.password.strip()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              SELECT id, password, nickname, balance, is_admin
              FROM users WHERE username=%s
            """, (un,))
            row = cur.fetchone()
            if not row or row[1] != pw:
                raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
            uid, _, nick, bal, is_admin_val = row
    return {
        "token": issue_token(uid),
        "id": uid,
        "username": un,
        "nickname": nick,
        "balance": int(bal),
        "is_admin": bool(is_admin_val),
    }

@app.get("/me")
def me(request: Request):
    token = extract_token(request)
    uid = parse_token(token)
    if not uid:
        raise HTTPException(status_code=401, detail="缺少或無效的 token")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              SELECT id, username, nickname, balance, is_admin, created_at
              FROM users WHERE id=%s
            """, (uid,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="找不到使用者")
            return {
                "id": row[0],
                "username": row[1],
                "nickname": row[2],
                "balance": int(row[3]),
                "is_admin": bool(row[4]),
                "created_at": row[5].isoformat() if row[5] else None,
            }

# 根路徑回 404；Swagger 在 /docs
@app.get("/")
def root_404():
    raise HTTPException(status_code=404, detail="API only. See /docs")
