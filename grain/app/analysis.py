"""预测、到期复盘、准确率统计、自我调整权重。

统计模型由两个信号组成:
  momentum  近 5 日均价相对 20 日均价的偏离 (趋势)
  seasonal  往年同期在同样周期内的平均涨跌 (季节规律, 如新粮上市压价)
每个信号的权重 = 它过去在已复盘预测里的方向准确率, 错得多权重就自动降低。
"""
import math
import statistics
from datetime import date, datetime, timedelta

from . import config, db


def today():
    return date.today().isoformat()


def series_name(key):
    if key in config.FUTURES:
        return config.FUTURES[key][2]
    parts = key.split(":")
    if len(parts) == 3 and parts[0] == "local":
        return f"{parts[2]}{config.CROPS.get(parts[1], parts[1])}收购价"
    return key


def crop_of(key):
    return "corn" if "corn" in key else "wheat" if "wheat" in key else None


def history(series, days=None):
    sql = "SELECT date, price FROM prices WHERE series=?"
    args = [series]
    if days:
        sql += " AND date>=?"
        args.append((date.today() - timedelta(days=days)).isoformat())
    return db.rows(sql + " ORDER BY date", args)


def all_series():
    out = []
    for r in db.rows("SELECT series, COUNT(*) n, MAX(date) last FROM prices GROUP BY series"):
        h = history(r["series"])
        last = h[-1]["price"]

        def chg(n):
            if len(h) > n:
                return round((last / h[-1 - n]["price"] - 1) * 100, 2)
        out.append({"series": r["series"], "name": series_name(r["series"]),
                    "last_date": r["last"], "last_price": round(last, 3), "points": r["n"],
                    "change_1": chg(1), "change_5": chg(5), "change_20": chg(20)})
    return out


def direction_of(pct):
    if abs(pct) < config.FLAT_PCT:
        return "flat"
    return "up" if pct > 0 else "down"


# ---------- 统计模型 ----------

def signal_weights():
    """按历史复盘结果给每个信号打分, 返回 {信号: 权重}。样本少时默认各 0.5。"""
    stats = {"momentum": [0, 0], "seasonal": [0, 0]}  # [对, 总]
    for p in db.rows("SELECT factors, actual_change_pct FROM predictions "
                     "WHERE source='stat' AND status='done'"):
        actual = direction_of(p["actual_change_pct"])
        for part in (p["factors"] or "").split(","):
            if "=" not in part:
                continue
            k, v = part.split("=")
            if k in stats:
                stats[k][1] += 1
                stats[k][0] += direction_of(float(v.rstrip("%"))) == actual
    w = {}
    for k, (ok, n) in stats.items():
        acc = (ok + 1) / (n + 2)  # 拉普拉斯平滑, 样本少时接近 0.5
        w[k] = round(max(0.05, acc), 3)
    return w


def stat_forecast(series, horizon):
    h = history(series)
    if len(h) < 30:
        return None
    prices = [x["price"] for x in h]
    last = prices[-1]
    ma5 = statistics.mean(prices[-5:])
    ma20 = statistics.mean(prices[-20:])
    momentum = (ma5 / ma20 - 1) * 100 * (horizon / 5) ** 0.5

    # 往年同期: 找每年同一日期(±7天)起步, horizon 天后的涨跌
    by_date = {x["date"]: x["price"] for x in h}
    base = date.fromisoformat(h[-1]["date"])
    seas = []
    for back in range(1, 6):
        try:
            start = base.replace(year=base.year - back)
        except ValueError:
            start = base - timedelta(days=365 * back)
        p0 = _near(by_date, start)
        p1 = _near(by_date, start + timedelta(days=horizon))
        if p0 and p1:
            seas.append((p1 / p0 - 1) * 100)
    seasonal = statistics.mean(seas) if seas else 0.0

    w = signal_weights()
    if not seas:
        w["seasonal"] = 0
    total = sum(w.values()) or 1
    expected = (momentum * w["momentum"] + seasonal * w["seasonal"]) / total

    rets = [prices[i] / prices[i - 1] - 1 for i in range(max(1, len(prices) - 60), len(prices))]
    vol = (statistics.pstdev(rets) if len(rets) > 1 else 0.01) * math.sqrt(horizon) * 100
    conf = min(0.85, 0.35 + abs(expected) / (vol + 1e-9) * 0.3)
    return {
        "series": series, "base_date": h[-1]["date"], "base_price": last,
        "horizon_days": horizon, "direction": direction_of(expected),
        "low": round(last * (1 + (expected - vol) / 100), 2),
        "high": round(last * (1 + (expected + vol) / 100), 2),
        "confidence": round(conf, 2),
        "factors": f"momentum={momentum:+.2f}%,seasonal={seasonal:+.2f}%",
        "reasoning": (f"趋势信号 {momentum:+.2f}% (权重{w['momentum']}), "
                      f"往年同期 {seasonal:+.2f}% (样本{len(seas)}年, 权重{w['seasonal']}), "
                      f"综合预期 {expected:+.2f}%, 波动±{vol:.2f}%"),
    }


def _near(by_date, d, span=7):
    for i in range(span + 1):
        for dd in (d + timedelta(days=i), d - timedelta(days=i)):
            if dd.isoformat() in by_date:
                return by_date[dd.isoformat()]
    return None


def save_prediction(p, source):
    base = date.fromisoformat(p["base_date"])
    target = (base + timedelta(days=int(p["horizon_days"]))).isoformat()
    return db.execute(
        "INSERT INTO predictions(created_at,source,series,base_date,base_price,horizon_days,"
        "target_date,direction,low,high,confidence,reasoning,factors) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), source, p["series"], p["base_date"],
         p["base_price"], p["horizon_days"], target, p["direction"], p.get("low"),
         p.get("high"), p.get("confidence"), p.get("reasoning"), p.get("factors")),
    )


def run_stat_predictions():
    n = 0
    for s in all_series():
        for hz in config.HORIZONS:
            dup = db.rows("SELECT 1 FROM predictions WHERE source='stat' AND series=? "
                          "AND horizon_days=? AND base_date=?", (s["series"], hz, s["last_date"]))
            if dup:
                continue
            f = stat_forecast(s["series"], hz)
            if f:
                save_prediction(f, "stat")
                n += 1
    return n


# ---------- 到期复盘 ----------

def evaluate_due():
    """到期预测和实际价格对比。实际价取目标日当天或之前最近一个交易日。"""
    done = 0
    for p in db.rows("SELECT * FROM predictions WHERE status='open' AND target_date<=?", (today(),)):
        later = db.rows("SELECT 1 FROM prices WHERE series=? AND date>=? LIMIT 1",
                        (p["series"], p["target_date"]))
        if not later:
            continue  # 数据还没更新到目标日, 等下一次
        a = db.rows("SELECT price FROM prices WHERE series=? AND date<=? AND date>? "
                    "ORDER BY date DESC LIMIT 1", (p["series"], p["target_date"], p["base_date"]))
        if not a:
            continue
        actual = a[0]["price"]
        pct = round((actual / p["base_price"] - 1) * 100, 2)
        correct = int(direction_of(pct) == p["direction"])
        in_range = None
        if p["low"] is not None and p["high"] is not None:
            in_range = int(p["low"] <= actual <= p["high"])
        review = None
        if p["source"] == "stat":
            review = f"实际 {pct:+.2f}%, 预测{_cn(p['direction'])}, {'正确' if correct else '错误'}; {p['factors']}"
        db.execute("UPDATE predictions SET status='done', actual_price=?, actual_change_pct=?, "
                   "correct=?, in_range=?, review=COALESCE(review, ?) WHERE id=?",
                   (actual, pct, correct, in_range, review, p["id"]))
        done += 1
    return done


def _cn(d):
    return {"up": "涨", "down": "跌", "flat": "平"}.get(d, d)


def accuracy(days=180):
    since = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for r in db.rows(
        "SELECT source, horizon_days, COUNT(*) n, SUM(correct) ok, SUM(in_range) inr, "
        "AVG(ABS(actual_change_pct)) avg_move FROM predictions "
        "WHERE status='done' AND target_date>=? GROUP BY source, horizon_days "
        "ORDER BY source, horizon_days", (since,)):
        r["direction_accuracy"] = round(r["ok"] / r["n"], 3) if r["n"] else None
        r["range_hit_rate"] = round((r["inr"] or 0) / r["n"], 3) if r["n"] else None
        out.append(r)
    return out


def needs_review():
    """GPT 做错但还没写复盘的预测, 下次对话时让 GPT 先补上教训。"""
    return db.rows("SELECT * FROM predictions WHERE source='gpt' AND status='done' "
                   "AND correct=0 AND review IS NULL ORDER BY target_date DESC LIMIT 10")


# ---------- 价格提醒 ----------

def check_alerts():
    fired = []
    latest = {s["series"]: s for s in all_series()}
    for a in db.rows("SELECT * FROM alerts WHERE active=1"):
        s = latest.get(a["series"])
        if not s:
            continue
        hit = s["last_price"] >= a["value"] if a["op"] == "above" else s["last_price"] <= a["value"]
        if hit:
            db.execute("UPDATE alerts SET active=0, triggered_at=? WHERE id=?",
                       (datetime.now().isoformat(timespec="seconds"), a["id"]))
            fired.append(f"{s['name']} {s['last_price']} 已{'突破' if a['op'] == 'above' else '跌破'} {a['value']}")
    return fired


# ---------- 给 GPT 的一次性上下文 ----------

def weather_summary():
    out = []
    for city in config.HENAN_CITIES:
        w = db.rows("SELECT * FROM weather WHERE city=? AND date>=? ORDER BY date LIMIT 10",
                    (city, today()))
        if w:
            out.append({"city": city,
                        "next10_precip_mm": round(sum(x["precip"] or 0 for x in w), 1),
                        "max_temp": max(x["tmax"] for x in w if x["tmax"] is not None),
                        "min_temp": min(x["tmin"] for x in w if x["tmin"] is not None)})
    return out


def context():
    return {
        "today": today(),
        "series": all_series(),
        "weather_henan_next10d": weather_summary(),
        "accuracy": accuracy(),
        "signal_weights": signal_weights(),
        "open_predictions": db.rows("SELECT id,source,series,base_price,horizon_days,target_date,"
                                    "direction,low,high,confidence FROM predictions "
                                    "WHERE status='open' ORDER BY target_date LIMIT 30"),
        "recent_results": db.rows("SELECT id,source,series,horizon_days,direction,"
                                  "actual_change_pct,correct,review FROM predictions "
                                  "WHERE status='done' ORDER BY target_date DESC LIMIT 15"),
        "needs_review": needs_review(),
        "lessons": db.rows("SELECT id,created_at,crop,content,tags FROM lessons "
                           "ORDER BY id DESC LIMIT 30"),
        "field_notes": db.rows("SELECT date,content FROM notes ORDER BY date DESC, id DESC LIMIT 15"),
    }
