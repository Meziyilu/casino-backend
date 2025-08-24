from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import psycopg

APP_NAME = "casino-backend"

app = FastAPI(title=APP_NAME)

# 開 CORS（先放寬，之後可改白名單）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DDL_USERS = """
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  tg_id TEXT UNIQUE,
  nickname TEXT,
  balance BIGINT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

DDL_ROUNDS = """
CREATE TABLE IF NOT EXISTS rounds (
  id BIGSERIAL PRIMARY KEY,
  round_no INT NOT NULL,
  opened_at TIMESTAMPTZ DEFAULT now(),
  player_total INT,
  banker_total INT,
  outcome TEXT  -- 'player' | 'banker' | 'tie'
);
"""

DDL_BETS = """
CREATE TABLE IF NOT EXISTS bets (
  id BIGSERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  round_no INT NOT NULL,
  side TEXT,        -- 'player' | 'banker' | 'tie'
  amount BIGINT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

def init_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        print("[init_db] DATABASE_URL is not set; skip migrations.")
        return

    # psycopg v3：DDL 需要 commit；用 autocommit=True 最簡單
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL_USERS)
            cur.execute(DDL_ROUNDS)
            cur.execute(DDL_BETS)
    print("[init_db] Tables ensured: users, rounds, bets")

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/")
def root():
    return {"message": f"{APP_NAME} running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/db-check")
def db_check():
    url = os.getenv("DATABASE_URL")
    if not url:
        return {"ok": False, "reason": "DATABASE_URL missing"}
    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                one = cur.fetchone()[0]
        return {"ok": one == 1}
    except Exception as e:
        return {"ok": False, "reason": str(e)}
