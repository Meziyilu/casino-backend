# app.py
import os
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="TOPZ Casino Backend")

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

# 掛載路由（注意順序：先建 app，再 import router）
from auth.api import router as auth_router
from baccarat.api import router as baccarat_router
from baccarat.sql import ensure_schema
from baccarat.service import launch_all_rooms

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(baccarat_router, prefix="/baccarat", tags=["baccarat"])

@app.get("/")
def root():
    return {"ok": True, "service": "TOPZ backend"}

@app.on_event("startup")
async def _boot():
    ensure_schema()
    asyncio.create_task(launch_all_rooms())  # 三房自動開局
