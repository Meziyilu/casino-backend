# auth/api.py
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
import os, time, hashlib, hmac, psycopg
from psycopg.rows import dict_row

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")

def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

# ---------- 安全雜湊（可日後換 bcrypt/argon2） ----------
def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()

def make_token(user_id: int, username: str) -> str:
    payload = f"{user_id}.{username}.{int(time.time())}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def parse_token(token: str):
    try:
        user_id_s, username, ts_s, sig = token.split(".")
        payload = f"{user_id_s}.{username}.{ts_s}"
        good = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(good, sig):
            return int(user_id_s), username
    except Exception:
        return None
    return None

def require_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing token")
    token = authorization.split(" ", 1)[1]
    parsed = parse_token(token)
    if not parsed:
        raise HTTPException(401, "bad token")
    return parsed  # (user_id, username)

# ---------- 資料表保護：確保欄位存在 ----------
def ensure_user_schema():
    with db() as conn, conn.cursor() as cur:
        # 確保必要欄位存在
        cur.execute("""
        ALTER TABLE users
          ADD COLUMN IF NOT EXISTS nickname TEXT,
          ADD COLUMN IF NOT EXISTS password_hash TEXT,
          ADD COLUMN IF NOT EXISTS balance BIGINT DEFAULT 0;
        """)
        # username 唯一索引
        cur.execute("""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = 'idx_users_username'
          ) THEN
            CREATE UNIQUE INDEX idx_users_username ON users (username);
          END IF;
        END $$;
        """)
        conn.commit()

# ---------- Schemas ----------
class RegisterBody(BaseModel):
    username: str
    password: str
    nickname: str | None = None

class LoginBody(BaseModel):
    username: str
    password: str

# ---------- Helpers ----------
def get_has_legacy_password(cur) -> bool:
    cur.execute("""
      SELECT EXISTS(
        SELECT 1 FROM information_schema.columns
        WHERE table_name='users' AND column_name='password'
      ) AS has_legacy;
    """)
    row = cur.fetchone()
    return bool(row["has_legacy"])

# ---------- Endpoints ----------
@router.post("/register")
def register(body: RegisterBody):
    ensure_user_schema()
    if not body.username or not body.password:
        raise HTTPException(422, "username/password required")

    with db() as conn, conn.cursor() as cur:
        try:
            cur.execute("""
              INSERT INTO users (username, nickname, password_hash, balance)
              VALUES (%s, %s, %s, 0)
              RETURNING id, username, nickname, balance;
            """, (body.username, body.nickname or body.username, hash_pw(body.password)))
            row = cur.fetchone()
            conn.commit()
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(409, "username already exists")
        except Exception as e:
            conn.rollback()
            raise HTTPException(500, f"db error: {e}")

    token = make_token(row["id"], row["username"])
    return {"ok": True, "token": token, "user": row}

@router.post("/login")
def login(body: LoginBody):
    ensure_user_schema()
    with db() as conn, conn.cursor() as cur:
        # 先偵測是否有舊欄位 password
        has_legacy = get_has_legacy_password(cur)

        if has_legacy:
            # 同時抓 password_hash 與舊的明碼 password（若存在）
            cur.execute("""
              SELECT id, username, nickname, balance, password_hash, password AS legacy_password
              FROM users WHERE username=%s;
            """, (body.username,))
        else:
            cur.execute("""
              SELECT id, username, nickname, balance, password_hash, NULL::text AS legacy_password
              FROM users WHERE username=%s;
            """, (body.username,))

        row = cur.fetchone()
        if not row:
            raise HTTPException(401, "user not found")

        # 驗證：優先用 hash；如果沒有 hash 但 legacy 明碼符合，則升級
        hashed = row["password_hash"]
        legacy = row["legacy_password"]
        ok = False
        upgrade_hash = None

        if hashed:
            ok = hmac.compare_digest(hash_pw(body.password), hashed)
        elif legacy is not None and body.password == legacy:
            ok = True
            upgrade_hash = hash_pw(body.password)

        if not ok:
            raise HTTPException(401, "wrong password")

        # 升級：把 legacy 明碼寫入 password_hash（並可選擇把舊 password 清掉）
        if upgrade_hash:
            try:
                cur.execute("UPDATE users SET password_hash=%s WHERE id=%s;", (upgrade_hash, row["id"]))
                # 如果你想把舊 password 清掉（避免保存明碼），解除下一行註解：
                # cur.execute("UPDATE users SET password=NULL WHERE id=%s;", (row["id"],))
                conn.commit()
            except Exception:
                conn.rollback()  # 升級失敗不影響這次登入

    token = make_token(row["id"], row["username"])
    return {"ok": True, "token": token}

@router.get("/me")
def me(user = Depends(require_user)):
    uid, _ = user
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, username, nickname, balance FROM users WHERE id=%s;", (uid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "user not found")
        return row
