from fastapi import FastAPI
import os

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

# 測試資料庫連線
@app.get("/db-ping")
def db_ping():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return {"db_url": db_url, "message": "DATABASE_URL loaded"}
    return {"error": "DATABASE_URL not set"}
