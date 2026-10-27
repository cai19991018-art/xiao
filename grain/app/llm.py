"""DeepSeek 分析 (只在后端调用, Key 配在 .env)。

学习闭环:
  1. 先把做错且没复盘的预测交给 DeepSeek, 写偏差原因和一条经验, 存进经验库
  2. 再带着经验库、准确率、统计模型参考、行情、天气、一线见闻做新预测, 存档等到期核验
"""
import json
import logging

import requests

from . import analysis, config, db

log = logging.getLogger("llm")

SYSTEM = """你是河南粮商的玉米、小麦行情分析师。用户在河南收粮、卖粮。
要求: 说大白话; 价格单位 元/斤; 结论明确但不夸大, 不承诺收益;
必须参考经验库里的教训, 遇到类似情况要在依据里说明; 参考各模型历史准确率;
本地收购价和一线见闻比期货更贴近实际, 优先参考。只输出 JSON。"""


def enabled():
    return bool(config.DEEPSEEK_API_KEY)


def chat(messages, timeout=120):
    r = requests.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions", timeout=timeout,
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
        json={"model": config.DEEPSEEK_MODEL, "messages": messages, "temperature": 0.3,
              "response_format": {"type": "json_object"}},
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def _ask(text, data):
    return chat([{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": text + "\n" + json.dumps(data, ensure_ascii=False, default=str)}])


def review_mistakes():
    todo = analysis.needs_review()
    if not todo:
        return 0
    out = _ask("下面是之前判断出现偏差的预测(含当时依据和实际涨跌)。逐条复盘。返回 JSON: "
               '{"reviews":[{"id":预测id,"review":"一句话偏差原因","lesson":"一条可复用、具体的经验"}]}',
               {"mistakes": [analysis.decode(p) for p in todo],
                "lessons": db.rows("SELECT content FROM lessons ORDER BY id DESC LIMIT 20")})
    ids = {p["id"]: p for p in todo}
    n = 0
    for r in out.get("reviews", []):
        p = ids.get(r.get("id"))
        if not p or not r.get("review"):
            continue
        db.execute("UPDATE predictions SET review=? WHERE id=?", (r["review"], p["id"]))
        if r.get("lesson"):
            db.execute("INSERT INTO lessons(created_at,prediction_id,crop,content) VALUES(?,?,?,?)",
                       (analysis.today(), p["id"], p["crop"], r["lesson"]))
        n += 1
    return n


def analyze(region, crop, horizon):
    stat = analysis.stat_forecast(region, crop, horizon)
    if not stat:
        return None
    m = analysis.market(region, crop, days=60)
    data = {
        "地区": f"{config.PROVINCE}{region}", "品种": config.CROPS[crop], "周期天数": horizon,
        "价格口径": m["basis"], "最新价": m["price"], "日期": m["date"],
        "近60天价格": [[x["date"], x["price"]] for x in m["history"]],
        "期货参考": m["futures"], "天气": m["weather"] and {k: m["weather"][k] for k in ("precip_10d", "tmax", "tmin")},
        "一线见闻": m["notes"], "统计模型参考": {k: stat[k] for k in ("stance", "low", "high", "signals")},
        "历史准确率": analysis.accuracy(),
        "经验库": db.rows("SELECT content FROM lessons WHERE crop=? OR crop IS NULL ORDER BY id DESC LIMIT 20", (crop,)),
        "近期核验结果": db.rows("SELECT horizon_days,direction,actual_change_pct,correct,review FROM predictions "
                          "WHERE crop=? AND status='done' ORDER BY target_date DESC LIMIT 10", (crop,)),
    }
    out = _ask(
        f"分析未来 {horizon} 天走势。返回 JSON: "
        '{"direction":"up|down|flat","stance":"上涨|震荡偏强|横盘震荡|震荡偏弱|下跌",'
        '"low":区间下限,"high":区间上限,"confidence":0到1,'
        f'"path":[未来{horizon}天每天的预测价, 共{horizon}个数],'
        '"factors":[{"type":"plus利多|minus利空|neutral中性","title":"8字内","detail":"一两句解释"}],'
        '"watch":"一句需关注的风险或重新评估条件"}  factors 2到4条。', data)
    if out.get("direction") not in ("up", "down", "flat"):
        raise ValueError(f"DeepSeek 返回格式不对: {out}")
    path = [float(x) for x in out.get("path") or []][:horizon]
    if len(path) != horizon:  # 不足就按区间中点线性补齐
        mid = (float(out.get("low") or stat["low"]) + float(out.get("high") or stat["high"])) / 2
        path = [round(stat["base_price"] + (mid - stat["base_price"]) * (i + 1) / horizon, 4) for i in range(horizon)]
    factors = [f for f in out.get("factors") or [] if isinstance(f, dict) and f.get("title")]
    for f in factors:
        f["type"] = f.get("type") if f.get("type") in ("plus", "minus", "neutral") else "neutral"
    p = dict(stat, direction=out["direction"], stance=out.get("stance") or stat["stance"],
             low=out.get("low", stat["low"]), high=out.get("high", stat["high"]),
             confidence=out.get("confidence", 0.5), path=path, factors=factors or stat["factors"],
             watch=out.get("watch") or stat["watch"], signals=None)
    return analysis.save_prediction(p, "deepseek")
