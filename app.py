import psycopg, os

def init_db():
    url = os.getenv("DATABASE_URL")
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
              id SERIAL PRIMARY KEY,
              tg_id TEXT UNIQUE,
              nickname TEXT,
              balance BIGINT DEFAULT 0,
              created_at TIMESTAMPTZ DEFAULT now()
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS rounds (
              id BIGSERIAL PRIMARY KEY,
              round_no INT NOT NULL,
              opened_at TIMESTAMPTZ DEFAULT now(),
              player_total INT,
              banker_total INT,
              outcome TEXT
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS bets (
              id BIGSERIAL PRIMARY KEY,
              user_id INT REFERENCES users(id),
              round_no INT NOT NULL,
              side TEXT,
              amount BIGINT NOT NULL,
              created_at TIMESTAMPTZ DEFAULT now()
            );
            """)

# 在 FastAPI 啟動事件呼叫
from fastapi import FastAPI

app = FastAPI()

@app.on_event("startup")
def startup_event():
    init_db()
