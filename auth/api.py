# auth/api.py
import os, bcrypt, jwt, psycopg
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

router = APIRouter()

SECRET = os.getenv("SECRET_KEY", "change-me")

def _conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(dsn, autocommit=True)

class RegIn(BaseModel):
    username: str
    password: str
    nickname: str | None = None

class LoginIn(BaseModel):
    username: str
    password: str

def _token(uid: int) -> str:
    payload = {"uid": uid, "exp": datetime.now(timezone.utc) + timedelta(days=7)}
    return jwt.encode(payload, SECRET, algorithm="HS256")

def _uid_from_token(auth: str | None) -> int | None:
    if not auth: return None
    # 支援 "Bearer xxx"
    parts = auth.split()
    tok = parts[-1] if len(parts) == 2 else auth
    try:
        data = jwt.decode(tok, SECRET, algorithms=["HS256"])
        return int(data["uid"])
    except Exception:
        return None

@router.post("/register")
def register(body: RegIn):
    if not body.username or not body.password:
        raise HTTPException(400, "username/password required")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username=%s;", (body.username,))
        if cur.fetchone():
            raise HTTPException(409, "username taken")
        ph = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
        cur.execute(
            "INSERT INTO users (username, password_hash, nickname, balance) VALUES (%s,%s,%s,%s) RETURNING id;",
            (body.username, ph, body.nickname or body.username, 10000),
        )
        uid = int(cur.fetchone()[0])
        return {"ok": True, "token": _token(uid)}

@router.post("/login")
def login(body: LoginIn):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, password_hash FROM users WHERE username=%s;", (body.username,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(401, "invalid credentials")
        uid, ph = int(row[0]), row[1]
        if not bcrypt.checkpw(body.password.encode(), ph.encode()):
            raise HTTPException(401, "invalid credentials")
        return {"ok": True, "token": _token(uid)}

@router.get("/me")
def me(authorization: str | None = Header(default=None)):
    uid = _uid_from_token(authorization)
    if not uid:
        raise HTTPException(401, "unauthorized")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, username, nickname, balance, is_admin FROM users WHERE id=%s;", (uid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "not found")
        return {
            "id": int(row[0]),
            "username": row[1],
            "nickname": row[2],
            "balance": int(row[3]),
            "is_admin": bool(row[4]),
        }
