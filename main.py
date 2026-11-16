import secrets
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

from config import settings, get_admin_api_secret
import db
from bot import bot, dp, expiration_worker, start_whitelabel_bots

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    extra_bots = await start_whitelabel_bots()
    all_bots = [bot] + extra_bots
    polling_task = asyncio.create_task(dp.start_polling(*all_bots, handle_signals=False))
    worker_task = asyncio.create_task(expiration_worker())
    yield
    polling_task.cancel()
    worker_task.cancel()
    for b in all_bots:
        await b.session.close()

app = FastAPI(title="VIP Channel SaaS v8 Ultimate", lifespan=lifespan, docs_url=None, redoc_url=None)

class PlanIn(BaseModel):
    chat_id: int
    owner_id: int
    title: str
    name: str
    duration_days: int
    price_toman: int
    original_price_toman: int = 0
    secret_key: str = ""

def _require_admin_secret(provided: str) -> None:
    expected = get_admin_api_secret()
    if not expected:
        raise HTTPException(status_code=503, detail="API غیرفعال است؛ ابتدا ADMIN_API_SECRET را در .env تنظیم کنید.")
    if not secrets.compare_digest(provided or "", expected):
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز: کلید امنیتی نامعتبر است.")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/channels/{chat_id}/plans")
async def api_get_plans(chat_id: int, x_admin_secret: str = Header(default="")):
    _require_admin_secret(x_admin_secret)
    ch = db.get_channel(chat_id)
    quota = db.get_admin_quota_status(ch["owner_id"]) if ch else {}
    return {"plans": db.get_channel_plans(chat_id), "stats": db.get_channel_stats(chat_id), "quota": quota}

@app.post("/api/plans")
async def api_create_plan(payload: PlanIn, x_admin_secret: str = Header(default="")):
    _require_admin_secret(payload.secret_key or x_admin_secret)
    if payload.duration_days <= 0 or payload.price_toman < 1000:
        raise HTTPException(status_code=400, detail="مدت زمان باید حداقل ۱ روز و مبلغ حداقل ۱۰۰۰ تومان باشد.")
    if not db.upsert_channel(payload.chat_id, payload.owner_id, payload.title):
        raise HTTPException(status_code=403, detail="این کانال متعلق به کاربر دیگری است.")
    plan_id = db.create_plan(
        payload.chat_id, payload.name, payload.duration_days,
        payload.price_toman, payload.original_price_toman
    )
    return {"status": "created", "plan_id": plan_id}

@app.get("/miniapp", response_class=HTMLResponse)
async def miniapp_ui(x_admin_secret: str = Header(default="")):
    _require_admin_secret(x_admin_secret)
    return HTMLResponse("<h2 dir='rtl'>مدیریت کانال از طریق ربات تلگرام فعال است.</h2>")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)
