# app.py — Auth + Lobby (with Debug endpoints)
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import os, psycopg, logging

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

APP_NAME = os.getenv("APP_NAME", "Casino Backend - Auth Only")
DATABASE_URL = os.environ.get("DATABASE_URL")  # 必填
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL")

ADMIN_USERS: set[str] = {
    x.strip().lower() for x in os.getenv("ADMIN_USERS", "").split(",") if x.strip()
}

# ---------- CORS ----------
def get_allowed_origins() -> List[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "https://topz0705.com",
        "https://casino-frontend-pya7.onrender.com",
        "http://localhost:5173",
    ]

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- DB ----------
def get_conn():
    # 建議使用 External Connection URL
    return psycopg.connect(DATABASE_URL, autocommit=False)

def init_db():
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
    log.info("[init_db] users table ensured")

init_db()

# ---------- Models ----------
class RegisterReq(BaseModel):
    username: str
    password: str
    nickname: Optional[str] = None

class LoginReq(BaseModel):
    username: str
    password: str

# ---------- Token helpers ----------
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

# ---------- Routes ----------
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
    try:
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
                    "id": uid, "username": un, "nickname": nick,
                    "balance": int(bal), "is_admin": bool(is_admin_val)
                }
            except psycopg.Error as e:
                conn.rollback()
                log.exception("register error")
                raise HTTPException(status_code=409, detail="使用者已存在")
    except HTTPException:
        raise
    except Exception as e:
        log.exception("register fatal")
        raise HTTPException(status_code=500, detail="server error")

@app.post("/auth/login")
def login(req: LoginReq):
    try:
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
            "token": issue_token(uid), "id": uid, "username": un,
            "nickname": nick, "balance": int(bal), "is_admin": bool(is_admin_val)
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("login fatal")
        # 這裡會把真實錯誤寫到 Render Logs，回前端 500
        raise HTTPException(status_code=500, detail="server error")

@app.get("/me")
def me(request: Request):
    token = extract_token(request)
    uid = parse_token(token)
    if not uid:
        raise HTTPException(status_code=401, detail="缺少或無效的 token")
    try:
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
                    "id": row[0], "username": row[1], "nickname": row[2],
                    "balance": int(row[3]), "is_admin": bool(row[4]),
                    "created_at": row[5].isoformat() if row[5] else None
                }
    except HTTPException:
        raise
    except Exception:
        log.exception("me fatal")
        raise HTTPException(status_code=500, detail="server error")

@app.get("/")
def root_404():
    raise HTTPException(status_code=404, detail="API only. See /docs")

# ---------- Debug endpoints ----------
@app.get("/debug/db")
def debug_db():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                one = cur.fetchone()[0]
        return {"ok": True, "db": one}
    except Exception as e:
        log.exception("debug_db")
        raise HTTPException(status_code=500, detail=f"db error: {repr(e)}")

@app.get("/debug/users_sample")
def debug_users_sample():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, username, balance, is_admin, created_at FROM users ORDER BY id ASC LIMIT 5;")
                rows = cur.fetchall()
        return {"ok": True, "count": len(rows), "users": [
            {"id": r[0], "username": r[1], "balance": int(r[2]), "is_admin": bool(r[3]),
             "created_at": r[4].isoformat() if r[4] else None}
            for r in rows
        ]}
    except Exception as e:
        log.exception("debug_users_sample")
        raise HTTPException(status_code=500, detail=f"db error: {repr(e)}")

@app.get("/debug/selftest")
def debug_selftest():
    report = {
        "app": APP_NAME,
        "time": datetime.now(timezone.utc).isoformat(),
        "allowed_origins": get_allowed_origins(),
        "db_ok": False,
        "users_table": False,
        "users_count": None,
    }
    try:
        with get_conn() as conn:
            report["db_ok"] = True
            with conn.cursor() as cur:
                cur.execute("""
                  SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name='users'
                  );
                """)
                exists = cur.fetchone()[0]
                report["users_table"] = bool(exists)
                if exists:
                    cur.execute("SELECT COUNT(*) FROM users;")
                    report["users_count"] = cur.fetchone()[0]
    except Exception as e:
        log.exception("debug_selftest")
    return report
