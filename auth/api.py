# auth/api.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import os, time, hashlib, hmac
import psycopg
from psycopg.rows import dict_row

router = APIRouter()

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")

def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

# ---- 簡單 SHA256 雜湊（可改 bcrypt/argon2；本版重點是修復運作） ----
def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()

def verify_pw(pw: str, hashed: str | None, legacy_plain: str | None) -> tuple[bool, str | None]:
    """
    回傳 (驗證是否通過, 若是 legacy_plain 匹配則回新 hash 以便升級)
    """
    if hashed:  # 正常情況：已有 hash
        return hmac.compare_digest(hash_pw(pw), hashed), None
    # 兼容舊資料：若資料庫還有明碼 password 欄位，且相符，就允許一次並回傳新的 hash 用來升級
    if legacy_plain is not None and pw == legacy_plain:
        return True, hash_pw(pw)
    return False, None

# ---- 極簡 JWT（可換 PyJWT；這裡手工做一個 HS256 token） ----
def make_token(user_id: int, username: str) -> str:
    # 這裡用超簡版 token（非標準 JWT），足夠前後端驗證使用
    payload = f"{user_id}.{username}.{int(time.time())}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def parse_token(token: str) -> tuple[int, str] | None:
    try:
        user_id_s, username, ts_s, sig = token.split(".")
        payload = f"{user_id_s}.{username}.{ts_s}"
        good = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(good, sig):
            return int(user_id_s), username
    except Exception:
        return None
    return None

def require_user(authorization: str | None = None):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing token")
    token = authorization.split(" ", 1)[1]
    parsed = parse_token(token)
    if not parsed:
        raise HTTPException(status_code=401, detail="bad token")
    return parsed  # (user_id, username)

# ---- Schemas ----
class RegisterBody(BaseModel):
    username: str
    password: str
    nickname: str | None = None

class LoginBody(BaseModel):
    username: str
    password: str

# ---- Endpoints ----
@router.post("/register")
def register(body: RegisterBody):
    if not body.username or not body.password:
        raise HTTPException(422, "username/password required")

    with db() as conn, conn.cursor() as cur:
        # 建帳號（username 唯一）
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
    return {"ok": True, "token": token, "user": {
        "id": row["id"], "username": row["username"],
        "nickname": row["nickname"], "balance": row["balance"]
    }}

@router.post("/login")
def login(body: LoginBody):
    with db() as conn, conn.cursor() as cur:
        # 兼容老資料：同時抓 password_hash、password（如果你以前有這欄）
        try:
            cur.execute("""
                SELECT id, username, nickname, balance,
                       password_hash,
                       -- 若真的有舊欄位 'password'（明碼），抓出來做一次升級
                       CASE WHEN EXISTS (
                           SELECT 1 FROM information_schema.columns
                           WHERE table_name='users' AND column_name='password'
                       ) THEN (SELECT password FROM users u2 WHERE u2.username = users.username)
                       ELSE NULL
                       END AS legacy_password
                FROM users
                WHERE username=%s;
            """, (body.username,))
            row = cur.fetchone()
        except Exception as e:
            raise HTTPException(500, f"db error: {e}")

        if not row:
            raise HTTPException(401, "user not found")

        ok, new_hash = verify_pw(body.password, row["password_hash"], row["legacy_password"])
        if not ok:
            raise HTTPException(401, "wrong password")

        # 若是從 legacy 明碼升級，這裡補寫 password_hash，並（可選）把舊 password 清成 NULL
        if new_hash:
            try:
                cur.execute("UPDATE users SET password_hash=%s WHERE id=%s;", (new_hash, row["id"]))
                # 可選：如果真有舊欄位 password，且你想清掉：
                # cur.execute("UPDATE users SET password=NULL WHERE id=%s;", (row["id"],))
                conn.commit()
            except Exception as e:
                conn.rollback()
                # 升級失敗不影響登入，只是下次再試；寫個 log 即可
                pass

    token = make_token(row["id"], row["username"])
    return {"ok": True, "token": token}

@router.get("/me")
def me(user = Depends(require_user)):
    uid, _uname = user
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, username, nickname, balance FROM users WHERE id=%s;", (uid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "user not found")
        return {"id": row["id"], "username": row["username"], "nickname": row["nickname"], "balance": row["balance"]}
