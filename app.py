# app.py
import os
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

APP_NAME = "TOPZ Casino Backend"

def get_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "https://topz0705.com",
        "https://casino-frontend-pya7.onrender.com",
        "http://localhost:5173",
    ]

app = FastAPI(title=APP_NAME)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),  # ⚠️ 不能用 "*"
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由（注意順序：先建 app，再 import router）
from auth.api import router as auth_router
from baccarat.api import router as baccarat_router
from baccarat.sql import ensure_schema
from baccarat.service import launch_all_rooms

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(baccarat_router, prefix="/baccarat", tags=["baccarat"])

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": APP_NAME}

@app.get("/")
def root():
    return {"ok": True, "service": APP_NAME}

@app.on_event("startup")
async def _boot():
    # 建表與索引（可重入）
    ensure_schema()
    # 啟動三房自動開局（advisory lock 確保單實例）
    asyncio.create_task(launch_all_rooms())
