# casino-backend

FastAPI + Postgres（Render）  
- 自動開局/鎖單/結算（下注 60s、開獎動畫等待 15s、間隔 3s）
- 百家樂主注賠率：閒 1:1、莊 0.95:1、和 8:1
- 回傳補牌旗標：player_draw3/banker_draw3，前端做翻牌動畫用

## Env
- DATABASE_URL=（Render Postgres 的 External Database URL）
- SECRET_KEY=隨機長字串
- ADMIN_USERS=逗號分隔帳號，如 admin,topz0705
- AUTO_DEAL=1
- AUTO_BET_SEC=60
- AUTO_REVEAL_SEC=15
- AUTO_GAP_SEC=3

## Run (local)
pip install -r requirements.txt
uvicorn app:app --reload
