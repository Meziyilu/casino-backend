# app.py
import os
import asyncio
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

log = logging.getLogger("uvicorn.error")

app = FastAPI(title="TOPZ Casino Backend")

# ---- CORS：直接放行前端網域（避免憑證 + CORS 再卡）----
ALLOWED = {
    "https://topz0705.com",
    "https://casino-frontend-pya7.onrender.com",
    "http://localhost:5173",
}
# 若你也想保留環境變數方式，可一起納入
_env = os.getenv("ALLOWED_ORIGINS", "")
if _env.strip():
    for o in _env.split(","):
        o = o.strip()
        if o:
            ALLOWED.add(o)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED),   # ⚠️ 不要用 "*"
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 掛載路由（先建 app 再 import router）----
#   你的專案結構需有：
#   auth/__init__.py, auth/api.py（內含 router）
#   baccarat/__init__.py, baccarat/api.py, baccarat/sql.py, baccarat/service.py
from auth.api import router as auth_router
app.include_router(auth_router, prefix="/auth", tags=["auth"])

# 百家樂路由（如果你先只想讓登入可用，也可以暫時註解掉下面兩行）
from baccarat.api import router as baccarat_router
app.include_router(baccarat_router, prefix="/baccarat", tags=["baccarat"])

# ---- 健康檢查 & 根路由 ----
@app.get("/")
def root():
    return {"ok": True, "service": "TOPZ backend"}

@app.get("/healthz")
def healthz():
    return {"ok": True, "cors": list(ALLOWED)}

# ---- 啟動：建表 & 啟動三房自動開局（失敗不影響 /auth）----
@app.on_event("startup")
async def _boot():
    # 建表
    try:
        from baccarat.sql import ensure_schema
        ensure_schema()
        log.info("[BOOT] ensure_schema OK")
    except Exception as e:
        log.error("[BOOT] ensure_schema failed: %s", e)

    # 啟動 dealer 背景任務
    async def _dealer_loop():
        try:
            from baccarat.service import launch_all_rooms
            await launch_all_rooms()
        except Exception as e:
            # 不要讓例外把整個 App 掛掉
            log.error("[DEALER] launch_all_rooms error: %s", e)

    # 背景開，不阻塞 Auth
    asyncio.create_task(_dealer_loop())
