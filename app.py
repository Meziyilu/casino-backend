from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os, psycopg

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 先放寬方便前端測試，上線再收斂
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "casino-backend running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/db-check")
def db_check():
    url = os.getenv("DATABASE_URL")
    if not url:
        return {"ok": False, "reason": "DATABASE_URL missing"}
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            one = cur.fetchone()[0]
    return {"ok": one == 1}
