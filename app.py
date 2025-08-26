# app.py
import os
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 先建 app（很重要，要在 include_router 之前）
app = FastAPI(title="TOPZ Casino Backend")

# ---------- CORS ----------
def get_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    # 預設允許你的網域與本機
    return [
        "https://topz0705.com",
        "https://casino-frontend-pya7.onrender.com",
        "http://localhost:5173",
        "http://localhost:3000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 掛載各模組 router ----------
# 這些 import 要放在 app 建立之後，避免循環引用
try:
    from auth.api import router as auth_router
    app.include_router(auth_router, prefix="/auth", tags=["auth"])
except Exception:
    pass  # 若你還沒拆 auth 模組，略過即可

try:
    from lobby.api import router as lobby_router
    app.include_router(lobby_router, prefix="/lobby", tags=["lobby"])
except Exception:
    pass

from baccarat.api import router as baccarat_router  # 你現有的百家樂 API
app.include_router(baccarat_router, prefix="/baccarat", tags=["baccarat"])

try:
    from admin.api import router as admin_router
    app.include_router(admin_router, prefix="/admin", tags=["admin"])
except Exception:
    pass

@app.get("/")
def root():
    return {"ok": True, "service": "TOPZ backend"}

# ---------- 啟動背景：自動開局三房 ----------
# 確保資料表、開 dealer loop
from baccarat.sql import ensure_schema
from baccarat.service import launch_all_rooms

@app.on_event("startup")
async def _boot():
    ensure_schema()
    asyncio.create_task(launch_all_rooms())

# （可選）關閉時若要停止 loop，可在這裡清理
