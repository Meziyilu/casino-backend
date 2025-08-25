# --- 這些 import 放在檔案頂部 ---
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
import jwt  # PyJWT
from fastapi import HTTPException, Depends, Header

# JWT 與密碼雜湊設定
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
JWT_ALG = "HS256"
JWT_EXP_MIN = 60 * 24 * 7  # 7天
SECRET = os.getenv("SECRET_KEY", "dev-secret")  # 記得在 Render 設定 SECRET_KEY

def hash_pw(p: str) -> str:
    return pwd_ctx.hash(p)

def verify_pw(p: str, h: str) -> bool:
    return pwd_ctx.verify(p, h)

def make_token(user_id: int, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXP_MIN)).timestamp())
    }
    return jwt.encode(payload, SECRET, algorithm=JWT_ALG)

def parse_token(token: str):
    try:
        return jwt.decode(token, SECRET, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
