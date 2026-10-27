"""接口服务。网页通过 /api/* 访问, 需带请求头 X-API-Key。
DeepSeek、行情采集、自动分析都在后端运行, 配置见 .env。"""
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import analysis, config, db, fetchers, llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")

STATIC = Path(__file__).parent / "static"
DEFAULT_PREFS = {"region": "周口", "crop": "corn", "unit": "jin"}
Crop = Literal["corn", "wheat"]


def auth(x_api_key: str = Header(default="")):
    if not config.API_KEY or x_api_key != config.API_KEY:
        raise HTTPException(401, "API Key 错误")


def prefs():
    return {**DEFAULT_PREFS, **(db.get("prefs") or {})}


def check_region(region):
    if region not in config.HENAN_CITIES:
        raise HTTPException(400, f"不支持的地区 {region}")


# ---------- 分析与定时任务 ----------

def generate(region, crop, horizon):
    """生成一条分析: 有 DeepSeek 用 DeepSeek, 否则用统计模型。统计模型始终存一条做对照。"""
    sid = analysis.ensure_stat(region, crop, horizon)
    if sid is None:
        raise HTTPException(400, "行情数据不足 20 天, 暂时无法分析")
    if llm.enabled():
        try:
            llm.analyze(region, crop, horizon)
        except Exception as e:
            log.warning("DeepSeek 分析失败, 使用统计模型: %s", e)
            db.put("last_ai_error", {"time": datetime.now().isoformat(timespec="minutes"), "error": str(e)[:300]})
    return analysis.latest_analysis(region, crop, horizon)


def job_fetch():
    fetchers.fetch_futures()


def job_daily():
    fetchers.fetch_weather()
    analysis.evaluate_due()
    if llm.enabled():
        try:
            llm.review_mistakes()
        except Exception as e:
            log.warning("DeepSeek 复盘失败: %s", e)
    if not config.AUTO_ANALYSIS:
        return
    p = prefs()
    for crop in config.CROPS:
        for hz in config.HORIZONS:
            try:
                generate(p["region"], crop, hz)
            except HTTPException as e:
                log.info("跳过 %s %s: %s", crop, hz, e.detail)
    db.put("last_auto_run", datetime.now().isoformat(timespec="minutes"))


def job_all():
    job_fetch()
    job_daily()


scheduler = BackgroundScheduler(timezone=config.TZ)


@asynccontextmanager
async def lifespan(_app):
    db.init()
    hh, mm = config.AUTO_ANALYSIS_TIME.split(":")
    # 国内期货 15:00 收盘, 夜盘 23:00; 美盘北京时间凌晨收盘
    scheduler.add_job(job_fetch, "cron", hour="7,15,23", minute=40)
    scheduler.add_job(job_daily, "cron", hour=int(hh), minute=int(mm))
    scheduler.start()
    if not db.rows("SELECT 1 FROM prices LIMIT 1"):
        threading.Thread(target=job_all, daemon=True).start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="粮见", version="2.0.0", lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)


# ---------- 请求体 ----------

class PriceIn(BaseModel):
    crop: Crop
    region: str
    price: float = Field(gt=0, description="元/斤")
    date: Optional[str] = None
    note: Optional[str] = None


class NoteIn(BaseModel):
    content: str
    region: Optional[str] = None


class PrefsIn(BaseModel):
    region: str
    crop: Crop
    unit: Literal["jin", "ton"]


class AnalyzeIn(BaseModel):
    region: str
    crop: Crop
    horizon: Literal[3, 7]


# ---------- 接口 ----------

@app.get("/api/meta", dependencies=[Depends(auth)])
def meta():
    return {"province": config.PROVINCE, "regions": list(config.HENAN_CITIES), "crops": config.CROPS,
            "horizons": config.HORIZONS, "prefs": prefs()}


@app.post("/api/prefs", dependencies=[Depends(auth)])
def save_prefs(p: PrefsIn):
    check_region(p.region)
    db.put("prefs", p.model_dump())
    return prefs()


@app.get("/api/status", dependencies=[Depends(auth)])
def status():
    last = db.rows("SELECT MAX(date) d FROM prices WHERE series LIKE 'futures:%'")[0]["d"]
    fresh = bool(last) and (datetime.now().date() - datetime.fromisoformat(last).date()).days <= 5
    return {
        "market": {"ok": fresh, "last_date": last, "last_fetch": db.get("last_fetch"),
                   "source": "新浪财经期货日线 + 本地手动报价", "weather": "Open-Meteo",
                   "local_min": config.LOCAL_MIN_POINTS},
        "deepseek": {"ok": llm.enabled(), "model": config.DEEPSEEK_MODEL if llm.enabled() else None,
                     "last_error": db.get("last_ai_error")},
        "auto": {"ok": config.AUTO_ANALYSIS, "time": config.AUTO_ANALYSIS_TIME,
                 "last_run": db.get("last_auto_run")},
    }


@app.get("/api/market", dependencies=[Depends(auth)])
def get_market(region: str, crop: Crop, days: int = 90):
    check_region(region)
    return analysis.market(region, crop, days)


@app.post("/api/prices", dependencies=[Depends(auth)])
def add_price(p: PriceIn):
    check_region(p.region)
    db.upsert_price(f"local:{p.crop}:{p.region}", p.date or analysis.today(), p.price, "元/斤", "manual", p.note)
    return {"ok": True}


@app.post("/api/notes", dependencies=[Depends(auth)])
def add_note(n: NoteIn):
    return {"id": db.execute("INSERT INTO notes(date,region,content) VALUES(?,?,?)",
                             (analysis.today(), n.region, n.content))}


@app.get("/api/analysis", dependencies=[Depends(auth)])
def get_analysis(region: str, crop: Crop, horizon: int = 7):
    check_region(region)
    a = analysis.latest_analysis(region, crop, horizon)
    series, scale, basis = analysis.resolve(region, crop)
    return {"analysis": a, "basis": basis, "history": analysis.history(series, scale, days=30)[-12:]}


@app.post("/api/analysis", dependencies=[Depends(auth)])
def post_analysis(q: AnalyzeIn):
    check_region(q.region)
    analysis.evaluate_due()
    generate(q.region, q.crop, q.horizon)
    return get_analysis(q.region, q.crop, q.horizon)


@app.get("/api/reviews", dependencies=[Depends(auth)])
def get_reviews(region: Optional[str] = None, crop: Optional[Crop] = None):
    return analysis.reviews(region, crop)


@app.post("/api/refresh", dependencies=[Depends(auth)])
def refresh():
    job_fetch()
    fetchers.fetch_weather()
    analysis.evaluate_due()
    return status()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
