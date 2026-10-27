"""接口服务。网页和 ChatGPT 专属 GPT 都通过 /api/* 访问, 需带请求头 X-API-Key。"""
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date
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


def auth(x_api_key: str = Header(default="")):
    if not config.API_KEY or x_api_key != config.API_KEY:
        raise HTTPException(401, "API Key 错误")


scheduler = BackgroundScheduler(timezone=config.TZ)


# ---------- 定时任务 ----------

def job_fetch():
    fetchers.fetch_futures()
    for msg in analysis.check_alerts():
        fetchers.push("粮价提醒", msg)


def job_daily():
    fetchers.fetch_weather()
    n = analysis.evaluate_due()
    m = analysis.run_stat_predictions()
    log.info("复盘 %d 条, 新统计预测 %d 条", n, m)
    llm.run_daily()


def job_all():
    job_fetch()
    job_daily()


@asynccontextmanager
async def lifespan(_app):
    db.init()
    # 国内期货 15:00 收盘, 夜盘 23:00; 美盘北京时间凌晨收盘
    scheduler.add_job(job_fetch, "cron", hour="7,15,23", minute=40)
    scheduler.add_job(job_daily, "cron", hour=16, minute=10)
    scheduler.start()
    if not db.rows("SELECT 1 FROM prices LIMIT 1"):
        threading.Thread(target=job_all, daemon=True).start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="河南粮价分析",
    version="1.0.0",
    description="玉米、小麦价格数据, 预测记录与复盘经验。",
    servers=[{"url": config.PUBLIC_URL}],
    lifespan=lifespan,
)


# ---------- 请求体 ----------

class PriceIn(BaseModel):
    crop: Literal["corn", "wheat"] = Field(description="corn=玉米, wheat=小麦")
    place: str = Field(description="地点, 如 周口、驻马店、本站")
    price: float = Field(description="价格, 单位 元/斤")
    date: Optional[str] = Field(None, description="YYYY-MM-DD, 默认今天")
    note: Optional[str] = Field(None, description="备注, 如 水分14%、容重")


class NoteIn(BaseModel):
    content: str = Field(description="一线见闻, 如 农户惜售、深加工厂提价2分")
    date: Optional[str] = None


class PredictionIn(BaseModel):
    series: str = Field(description="序列名, 必须是 getContext 里 series 中已有的")
    horizon_days: int = Field(description="预测周期天数, 常用 7/14/30")
    direction: Literal["up", "down", "flat"] = Field(description="up涨 down跌 flat平(涨跌<0.5%)")
    low: Optional[float] = Field(None, description="预测区间下限")
    high: Optional[float] = Field(None, description="预测区间上限")
    confidence: float = Field(description="把握 0-1")
    reasoning: str = Field(description="理由")
    factors: Optional[str] = Field(None, description="关键因素, 逗号分隔, 如 天气,政策拍卖,生猪")


class ReviewIn(BaseModel):
    review: str = Field(description="复盘: 错在哪里, 忽略了什么因素")
    lesson: Optional[str] = Field(None, description="可推广的经验教训, 会存入经验库")
    tags: Optional[str] = None


class LessonIn(BaseModel):
    content: str
    crop: Optional[Literal["corn", "wheat"]] = None
    tags: Optional[str] = None


class AskIn(BaseModel):
    question: str
    history: list[dict] = []


class AlertIn(BaseModel):
    series: str
    op: Literal["above", "below"] = Field(description="above 涨破 / below 跌破")
    value: float


# ---------- 接口 ----------

@app.get("/api/context", operation_id="getContext", dependencies=[Depends(auth)],
         summary="分析前必读: 最新行情、河南天气、准确率、待复盘的错误、经验库、一线见闻")
def get_context():
    return analysis.context()


@app.get("/api/prices", operation_id="getPrices", dependencies=[Depends(auth)],
         summary="某序列的历史价格")
def get_prices(series: str, days: int = 120):
    return {"series": series, "name": analysis.series_name(series),
            "data": analysis.history(series, days)}


@app.post("/api/prices", operation_id="addLocalPrice", dependencies=[Depends(auth)],
          summary="记录本地收购价")
def add_price(p: PriceIn):
    key = f"local:{p.crop}:{p.place}"
    db.upsert_price(key, p.date or analysis.today(), p.price, "元/斤", "manual", p.note)
    return {"ok": True, "series": key}


@app.post("/api/notes", operation_id="addNote", dependencies=[Depends(auth)],
          summary="记录一线见闻")
def add_note(n: NoteIn):
    return {"id": db.execute("INSERT INTO notes(date,content) VALUES(?,?)",
                             (n.date or analysis.today(), n.content))}


@app.get("/api/predictions", operation_id="listPredictions", dependencies=[Depends(auth)],
         summary="预测记录")
def list_predictions(status: Optional[Literal["open", "done"]] = None,
                     source: Optional[Literal["stat", "gpt"]] = None, limit: int = 50):
    sql, args = "SELECT * FROM predictions WHERE 1=1", []
    if status:
        sql += " AND status=?"; args.append(status)
    if source:
        sql += " AND source=?"; args.append(source)
    return db.rows(sql + " ORDER BY id DESC LIMIT ?", args + [limit])


@app.post("/api/predictions", operation_id="createPrediction", dependencies=[Depends(auth)],
          summary="保存 GPT 的预测, 到期后系统自动对比实际价格")
def create_prediction(p: PredictionIn):
    h = analysis.history(p.series)
    if not h:
        raise HTTPException(400, f"没有序列 {p.series} 的数据")
    data = p.model_dump()
    data.update(base_date=h[-1]["date"], base_price=h[-1]["price"])
    return {"id": analysis.save_prediction(data, "gpt"), "base_price": h[-1]["price"],
            "base_date": h[-1]["date"]}


@app.post("/api/predictions/{pid}/review", operation_id="reviewPrediction",
          dependencies=[Depends(auth)], summary="给已到期的预测写复盘, 可同时存一条经验")
def review_prediction(pid: int, r: ReviewIn):
    p = db.rows("SELECT * FROM predictions WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "预测不存在")
    db.execute("UPDATE predictions SET review=? WHERE id=?", (r.review, pid))
    lid = None
    if r.lesson:
        lid = db.execute("INSERT INTO lessons(created_at,prediction_id,crop,content,tags) "
                         "VALUES(?,?,?,?,?)", (analysis.today(), pid,
                                               analysis.crop_of(p[0]["series"]), r.lesson, r.tags))
    return {"ok": True, "lesson_id": lid}


@app.post("/api/lessons", operation_id="addLesson", dependencies=[Depends(auth)],
          summary="直接加一条经验")
def add_lesson(l: LessonIn):
    return {"id": db.execute("INSERT INTO lessons(created_at,crop,content,tags) VALUES(?,?,?,?)",
                             (analysis.today(), l.crop, l.content, l.tags))}


@app.get("/api/alerts", operation_id="listAlerts", dependencies=[Depends(auth)],
         summary="价格提醒列表")
def list_alerts():
    return db.rows("SELECT * FROM alerts ORDER BY id DESC")


@app.post("/api/alerts", operation_id="addAlert", dependencies=[Depends(auth)],
          summary="设置价格提醒, 触发后推送到微信")
def add_alert(a: AlertIn):
    return {"id": db.execute("INSERT INTO alerts(series,op,value) VALUES(?,?,?)",
                             (a.series, a.op, a.value))}


@app.post("/api/refresh", operation_id="refreshData", dependencies=[Depends(auth)],
          summary="立即重新采集行情并复盘")
def refresh():
    job_all()
    return {"ok": True, "series": analysis.all_series()}


@app.get("/api/reports", operation_id="listReports", dependencies=[Depends(auth)],
         summary="AI 每日分析报告")
def list_reports(limit: int = 10):
    return db.rows("SELECT * FROM reports ORDER BY id DESC LIMIT ?", (limit,))


@app.post("/api/ask", include_in_schema=False, dependencies=[Depends(auth)])
def ask(q: AskIn):
    if not llm.enabled():
        raise HTTPException(400, "未配置 DEEPSEEK_API_KEY")
    try:
        return {"answer": llm.ask(q.question, q.history)}
    except Exception as e:
        raise HTTPException(502, f"DeepSeek 调用失败: {e}")


@app.post("/api/analyze", include_in_schema=False, dependencies=[Depends(auth)])
def analyze_now():
    if not llm.enabled():
        raise HTTPException(400, "未配置 DEEPSEEK_API_KEY")
    analysis.evaluate_due()
    llm.review_mistakes()
    return {"report": llm.daily_analysis()}


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True, "date": date.today().isoformat()}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
