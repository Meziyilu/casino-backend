import os
import psycopg
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import pytz

APP_NAME = "TOPZ Casino Backend"
DB_URL = os.getenv("DATABASE_URL")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme")

# ---------- DB ----------
def get_conn():
    return psycopg.connect(DB_URL, autocommit=True)

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT NOT NULL,
            nickname TEXT,
            balance BIGINT DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        """)
    print("✅ DB 初始化完成")

# ---------- 時區 ----------
TZ = pytz.timezone("Asia/Taipei")
def now_taipei():
    return datetime.now(TZ)

# ---------- Schemas ----------
class RegisterReq(BaseModel):
    username: str
    password: str
    nickname: str | None = None

class LoginReq(BaseModel):
    username: str
    password: str

class AdminGiveReq(BaseModel):
    username: str
    amount: int

# ---------- FastAPI ----------
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

# ---------- Auth ----------
@app.post("/auth/register")
def register(data: RegisterReq):
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (username, password, nickname) VALUES (%s,%s,%s) RETURNING id, username, nickname, balance",
                (data.username, data.password, data.nickname)
            )
            row = cur.fetchone()
            return {"user": {"id": row[0], "username": row[1], "nickname": row[2], "balance": row[3]}}
        except Exception as e:
            raise HTTPException(400, f"註冊失敗: {e}")

@app.post("/auth/login")
def login(data: LoginReq):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, username, password, nickname, balance FROM users WHERE username=%s", (data.username,))
        row = cur.fetchone()
        if not row or row[2] != data.password:
            raise HTTPException(401, "帳號或密碼錯誤")
        return {"user": {"id": row[0], "username": row[1], "nickname": row[3], "balance": row[4]}}

# ---------- 大廳 ----------
@app.get("/lobby/summary")
def lobby_summary():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(balance),0) FROM users")
        total_users, total_balance = cur.fetchone()
        # 今日新增
        cur.execute("SELECT COUNT(*) FROM users WHERE created_at::date = now()::date")
        today_new, = cur.fetchone()
        return {"summary": {
            "totalUsers": total_users,
            "totalBalance": int(total_balance),
            "todayNewUsers": today_new
        }}

# ---------- 管理 ----------
@app.post("/admin/give")
def admin_give(req: Request, data: AdminGiveReq):
    token = req.headers.get("Authorization","").replace("Bearer ","")
    if token != ADMIN_TOKEN:
        raise HTTPException(403, "Forbidden")

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance = balance + %s WHERE username=%s RETURNING id", (data.amount, data.username))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "找不到使用者")
        return {"ok": True, "username": data.username, "amount": data.amount}

# ---------- 啟動 ----------
@app.on_event("startup")
def on_startup():
    init_db()
    print("🎲 Casino backend ready:", now_taipei())

@app.get("/")
def root():
    return {"msg": "Casino backend OK", "time": str(now_taipei())}
