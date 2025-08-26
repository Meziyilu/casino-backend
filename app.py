# app.py
import os
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ---- 先建立 app（很重要，要在 include_router 之前）----
app = FastAPI(title="TOPZ Casino Backend")

# ---- CORS ----
def get_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    # 預設允許你的網域與本機
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

# ---- (之後) 再 import 百家樂模組並掛上 router ----
# 這些 import 要放在 app 建立之後，避免循環 import 或尚未定義 app
from baccarat.api import router as baccarat_router
from baccarat.sql import ensure_schema
from baccarat.service import launch_all_rooms

app.include_router(baccarat_router)

# ---- 你原本的認證 / 大廳等 API（如果有獨立 router 也可以 app.include_router(...) 掛上）----
# 例如：
# from auth.api import router as auth_router
# app.include_router(auth_router)

@app.get("/")
def root():
    return {"ok": True, "service": "TOPZ backend"}

# 啟動時做 DB 檢查、啟動三房自動開局背景任務
@app.on_event("startup")
async def _boot():
    ensure_schema()
    asyncio.create_task(launch_all_rooms())
