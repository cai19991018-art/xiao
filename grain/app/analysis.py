"""行情整理、统计预测、到期核验、自我调整权重。

价格统一用 元/斤。某地区某品种的价格序列:
  近 60 天本地报价 >= LOCAL_MIN_POINTS 条 -> 用本地收购价
  否则 -> 用期货主力价 ÷ 2000 折算 (标注"期货折算")

统计模型由两个信号组成:
  momentum  近 5 日均价相对 20 日均价的偏离 (趋势)
  seasonal  往年同期在同样周期内的平均涨跌 (季节规律, 如新粮上市压价)
每个信号的权重 = 它在已核验预测里的方向准确率, 错得多权重就自动降低。
"""
import json
import math
import statistics
from datetime import date, datetime, timedelta

from . import config, db

DIR_CN = {"up": "上涨", "down": "下跌", "flat": "持平"}


def today():
    return date.today().isoformat()


def direction_of(pct):
    if abs(pct) < config.FLAT_PCT:
        return "flat"
    return "up" if pct > 0 else "down"


def stance_of(pct, vol):
    """把预期涨跌幅换成口语化判断。"""
    strong = max(1.5, vol * 0.8)
    if pct >= strong:
        return "上涨"
    if pct >= config.FLAT_PCT:
        return "震荡偏强"
    if pct > -config.FLAT_PCT:
        return "横盘震荡"
    if pct > -strong:
        return "震荡偏弱"
    return "下跌"


# ---------- 价格序列 ----------

def resolve(region, crop):
    """返回 (序列名, 折算系数, 口径说明)。"""
    local = f"local:{crop}:{region}"
    since = (date.today() - timedelta(days=60)).isoformat()
    n = db.rows("SELECT COUNT(*) n FROM prices WHERE series=? AND date>=?", (local, since))[0]["n"]
    if n >= config.LOCAL_MIN_POINTS:
        return local, 1.0, "本地报价"
    return config.PROXY[crop], 1 / 2000, "期货折算"


def history(series, scale=1.0, days=None):
    sql, args = "SELECT date, price FROM prices WHERE series=?", [series]
    if days:
        sql += " AND date>=?"
        args.append((date.today() - timedelta(days=days)).isoformat())
    return [{"date": r["date"], "price": round(r["price"] * scale, 4)}
            for r in db.rows(sql + " ORDER BY date", args)]


def pct_change(h, n):
    if len(h) > n and h[-1 - n]["price"]:
        return round((h[-1]["price"] / h[-1 - n]["price"] - 1) * 100, 2)
    return None


def futures_refs():
    out = []
    for key, (_code, _kind, name, unit) in config.FUTURES.items():
        h = history(key, days=40)
        if h:
            out.append({"series": key, "name": name, "unit": unit, "price": h[-1]["price"],
                        "date": h[-1]["date"], "change_1": pct_change(h, 1), "change_5": pct_change(h, 5)})
    return out


def weather(city):
    w = db.rows("SELECT * FROM weather WHERE city=? AND date>=? ORDER BY date LIMIT 10", (city, today()))
    if not w:
        return None
    return {"city": city, "days": w,
            "precip_10d": round(sum(x["precip"] or 0 for x in w), 1),
            "tmax": max((x["tmax"] for x in w if x["tmax"] is not None), default=None),
            "tmin": min((x["tmin"] for x in w if x["tmin"] is not None), default=None)}


def market(region, crop, days=90):
    series, scale, basis = resolve(region, crop)
    h = history(series, scale, days)
    local = history(f"local:{crop}:{region}", 1.0, days)
    return {
        "region": region, "crop": crop, "basis": basis, "series": series,
        "price": h[-1]["price"] if h else None, "date": h[-1]["date"] if h else None,
        "change_1": pct_change(h, 1), "change_5": pct_change(h, 5), "change_20": pct_change(h, 20),
        "history": h, "local": local[-10:],
        "futures": futures_refs(), "weather": weather(region),
        "notes": db.rows("SELECT date, content FROM notes WHERE region=? OR region IS NULL "
                         "ORDER BY date DESC, id DESC LIMIT 10", (region,)),
    }


# ---------- 统计模型 ----------

def signal_weights():
    stats = {"momentum": [0, 0], "seasonal": [0, 0]}  # [对, 总]
    for p in db.rows("SELECT signals, actual_change_pct FROM predictions "
                     "WHERE source='stat' AND status='done' AND signals IS NOT NULL"):
        actual = direction_of(p["actual_change_pct"])
        for part in p["signals"].split(","):
            k, _, v = part.partition("=")
            if k in stats:
                stats[k][1] += 1
                stats[k][0] += direction_of(float(v.rstrip("%"))) == actual
    return {k: round(max(0.05, (ok + 1) / (n + 2)), 3) for k, (ok, n) in stats.items()}


def _near(by_date, d, span=7):
    for i in range(span + 1):
        for dd in (d + timedelta(days=i), d - timedelta(days=i)):
            if dd.isoformat() in by_date:
                return by_date[dd.isoformat()]
    return None


def stat_forecast(region, crop, horizon):
    series, scale, basis = resolve(region, crop)
    h = history(series, scale)
    if len(h) < 20:
        return None
    prices = [x["price"] for x in h]
    last = prices[-1]
    momentum = (statistics.mean(prices[-5:]) / statistics.mean(prices[-20:]) - 1) * 100 * (horizon / 5) ** 0.5

    by_date = {x["date"]: x["price"] for x in h}
    base = date.fromisoformat(h[-1]["date"])
    seas = []
    for back in range(1, 6):
        start = base - timedelta(days=round(365.25 * back))
        p0, p1 = _near(by_date, start), _near(by_date, start + timedelta(days=horizon))
        if p0 and p1:
            seas.append((p1 / p0 - 1) * 100)
    seasonal = statistics.mean(seas) if seas else 0.0

    w = signal_weights()
    if not seas:
        w["seasonal"] = 0
    expected = (momentum * w["momentum"] + seasonal * w["seasonal"]) / (sum(w.values()) or 1)

    rets = [prices[i] / prices[i - 1] - 1 for i in range(max(1, len(prices) - 60), len(prices))]
    vol = (statistics.pstdev(rets) if len(rets) > 1 else 0.01) * math.sqrt(horizon) * 100

    factors = [{
        "type": "plus" if momentum > config.FLAT_PCT else "minus" if momentum < -config.FLAT_PCT else "neutral",
        "title": "近期走势" + ("走强" if momentum > 0 else "走弱"),
        "detail": f"近5日均价较20日均价{'高' if momentum > 0 else '低'}，趋势信号 {momentum:+.2f}%（权重 {w['momentum']}）。",
    }]
    if seas:
        factors.append({
            "type": "plus" if seasonal > config.FLAT_PCT else "minus" if seasonal < -config.FLAT_PCT else "neutral",
            "title": "往年同期" + ("多为上涨" if seasonal > 0 else "多为下跌"),
            "detail": f"近{len(seas)}年同期 {horizon} 天平均 {seasonal:+.2f}%（权重 {w['seasonal']}）。",
        })
    wx = weather(region)
    if wx and wx["precip_10d"] >= 30:
        factors.append({"type": "neutral", "title": "近期降雨偏多",
                        "detail": f"{region}未来10天累计降水约 {wx['precip_10d']}mm，注意晾晒和粮质、上量节奏。"})
    return {
        "region": region, "crop": crop, "series": series, "scale": scale, "basis": basis,
        "base_date": h[-1]["date"], "base_price": last, "horizon_days": horizon,
        "direction": direction_of(expected), "stance": stance_of(expected, vol),
        "low": round(last * (1 + (expected - vol) / 100), 4),
        "high": round(last * (1 + (expected + vol) / 100), 4),
        "confidence": round(min(0.85, 0.35 + abs(expected) / (vol + 1e-9) * 0.3), 2),
        "path": [round(last * (1 + expected / 100 * (i + 1) / horizon), 4) for i in range(horizon)],
        "factors": factors,
        "watch": f"若价格跌破 {last * (1 - vol / 100):.3f} 或升破 {last * (1 + vol / 100):.3f}，需重新评估。",
        "signals": f"momentum={momentum:+.2f}%,seasonal={seasonal:+.2f}%",
    }


def save_prediction(p, source):
    base = date.fromisoformat(p["base_date"])
    target = (base + timedelta(days=int(p["horizon_days"]))).isoformat()
    return db.execute(
        "INSERT INTO predictions(created_at,source,region,crop,series,scale,basis,base_date,base_price,"
        "horizon_days,target_date,direction,stance,low,high,confidence,path,factors,watch,signals) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), source, p["region"], p["crop"], p["series"],
         p["scale"], p["basis"], p["base_date"], p["base_price"], int(p["horizon_days"]), target,
         p["direction"], p.get("stance"), p.get("low"), p.get("high"), p.get("confidence"),
         json.dumps(p.get("path") or []), json.dumps(p.get("factors") or [], ensure_ascii=False),
         p.get("watch"), p.get("signals")),
    )


def ensure_stat(region, crop, horizon):
    """统计模型每个交易日每个组合只存一条, 用于学习和对照。"""
    f = stat_forecast(region, crop, horizon)
    if not f:
        return None
    dup = db.rows("SELECT id FROM predictions WHERE source='stat' AND region=? AND crop=? "
                  "AND horizon_days=? AND base_date=? AND series=?",
                  (region, crop, horizon, f["base_date"], f["series"]))
    return dup[0]["id"] if dup else save_prediction(f, "stat")


# ---------- 到期核验 ----------

def evaluate_due():
    """实际价取目标日当天或之前最近一个有价格的日子 (同一序列、同一口径)。"""
    done = 0
    for p in db.rows("SELECT * FROM predictions WHERE status='open' AND target_date<=?", (today(),)):
        if not db.rows("SELECT 1 FROM prices WHERE series=? AND date>=? LIMIT 1", (p["series"], p["target_date"])):
            continue  # 数据还没到目标日
        a = db.rows("SELECT price FROM prices WHERE series=? AND date<=? AND date>? ORDER BY date DESC LIMIT 1",
                    (p["series"], p["target_date"], p["base_date"]))
        if not a:
            continue
        actual = round(a[0]["price"] * p["scale"], 4)
        pct = round((actual / p["base_price"] - 1) * 100, 2)
        correct = int(direction_of(pct) == p["direction"])
        in_range = int(p["low"] <= actual <= p["high"]) if p["low"] is not None and p["high"] is not None else None
        review = None
        if p["source"] == "stat" and not correct:
            review = f"实际{DIR_CN[direction_of(pct)]} {pct:+.2f}%，统计信号（{p['signals']}）未能反映。"
        db.execute("UPDATE predictions SET status='done', actual_price=?, actual_change_pct=?, correct=?, "
                   "in_range=?, review=COALESCE(review, ?) WHERE id=?",
                   (actual, pct, correct, in_range, review, p["id"]))
        done += 1
    return done


def accuracy(days=180):
    since = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for r in db.rows("SELECT source, horizon_days, COUNT(*) n, SUM(correct) ok, SUM(in_range) inr "
                     "FROM predictions WHERE status='done' AND target_date>=? "
                     "GROUP BY source, horizon_days ORDER BY source, horizon_days", (since,)):
        r["direction_accuracy"] = round(r["ok"] / r["n"], 3)
        r["range_hit_rate"] = round((r["inr"] or 0) / r["n"], 3)
        out.append(r)
    return out


def needs_review():
    return db.rows("SELECT * FROM predictions WHERE source!='stat' AND status='done' "
                   "AND correct=0 AND review IS NULL ORDER BY target_date DESC LIMIT 10")


def decode(p):
    p = dict(p)
    p["path"] = json.loads(p["path"] or "[]")
    p["factors"] = json.loads(p["factors"] or "[]")
    return p


def latest_analysis(region, crop, horizon):
    """优先显示 DeepSeek 的最新分析, 没有则显示统计模型。"""
    r = db.rows("SELECT * FROM predictions WHERE region=? AND crop=? AND horizon_days=? "
                "ORDER BY base_date DESC, (source='deepseek') DESC, id DESC LIMIT 1", (region, crop, horizon))
    return decode(r[0]) if r else None


def reviews(region=None, crop=None, limit=50):
    sql, args = "SELECT * FROM predictions WHERE source!='stat'", []
    # 没有 AI 预测时, 也展示统计模型的记录
    if not db.rows("SELECT 1 FROM predictions WHERE source!='stat' LIMIT 1"):
        sql = "SELECT * FROM predictions WHERE 1=1"
    if region:
        sql += " AND region=?"; args.append(region)
    if crop:
        sql += " AND crop=?"; args.append(crop)
    items = [decode(p) for p in db.rows(sql + " ORDER BY base_date DESC, id DESC LIMIT ?", args + [limit])]
    return {"checked": sum(p["status"] == "done" for p in items),
            "pending": sum(p["status"] == "open" for p in items),
            "items": items, "accuracy": accuracy(), "signal_weights": signal_weights(),
            "lessons": db.rows("SELECT * FROM lessons WHERE ?='' OR crop=? OR crop IS NULL "
                               "ORDER BY id DESC LIMIT 20", (crop or "", crop or ""))}
