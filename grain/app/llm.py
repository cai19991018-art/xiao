"""DeepSeek 自动分析: 每日报告 + 预测存档 + 错误复盘 + 网页对话。

学习闭环:
  1. 每天先把做错且没复盘的预测交给 DeepSeek, 让它写错因和一条经验, 存进经验库
  2. 再带着经验库、准确率、最新行情和天气做新的预测, 存档等到期验证
"""
import json
import logging
from datetime import datetime

import requests

from . import analysis, config, db

log = logging.getLogger("llm")

SYSTEM = """你是河南粮商的玉米、小麦行情分析师。用户在河南收粮、卖粮。
要求: 说大白话; 价格优先换算成 元/斤 (期货 元/吨 ÷ 2000); 结论明确但不夸大, 不承诺收益;
必须参考经验库里的教训, 遇到类似情况要明说; 参考各模型历史准确率, 谁准信谁;
本地收购价和一线见闻比期货更贴近实际, 优先参考。"""


def enabled():
    return bool(config.DEEPSEEK_API_KEY)


def chat(messages, json_mode=False, timeout=120):
    body = {"model": config.DEEPSEEK_MODEL, "messages": messages, "temperature": 0.3}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(f"{config.DEEPSEEK_BASE_URL}/chat/completions", json=body, timeout=timeout,
                      headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _ctx_text(ctx):
    return json.dumps(ctx, ensure_ascii=False, default=str)


def review_mistakes():
    todo = analysis.needs_review()
    if not todo:
        return 0
    ask = ("下面是你之前做错的预测(含当时理由和实际涨跌), 以及当时之后的行情。逐条复盘: 错在哪里、忽略了什么。"
           '返回 JSON: {"reviews":[{"id":预测id,"review":"错因","lesson":"一条可复用的经验, 具体可操作"}]}\n'
           + _ctx_text({"mistakes": todo, "lessons": analysis.context()["lessons"]}))
    out = json.loads(chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": ask}],
                          json_mode=True))
    ids = {p["id"]: p for p in todo}
    n = 0
    for r in out.get("reviews", []):
        p = ids.get(r.get("id"))
        if not p or not r.get("review"):
            continue
        db.execute("UPDATE predictions SET review=? WHERE id=?", (r["review"], p["id"]))
        if r.get("lesson"):
            db.execute("INSERT INTO lessons(created_at,prediction_id,crop,content,tags) VALUES(?,?,?,?,?)",
                       (analysis.today(), p["id"], analysis.crop_of(p["series"]), r["lesson"], "自动复盘"))
        n += 1
    return n


def daily_analysis():
    ctx = analysis.context()
    series = {s["series"]: s for s in ctx["series"]}
    if not series:
        return None
    ask = ("根据以下数据做今天的玉米、小麦行情分析。series 只能从 series 列表里选, 优先本地收购价序列。"
           "每个品种给 7 天和 30 天两个预测。返回 JSON:\n"
           '{"report":"Markdown 报告: ①结论 ②主要理由(≤3条) ③风险 ④收粮/卖粮操作建议",'
           '"predictions":[{"series":"","horizon_days":7,"direction":"up|down|flat",'
           '"low":0,"high":0,"confidence":0.6,"reasoning":"","factors":"逗号分隔"}]}\n'
           + _ctx_text(ctx))
    out = json.loads(chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": ask}],
                          json_mode=True))
    for p in out.get("predictions", []):
        s = series.get(p.get("series"))
        if not s or p.get("direction") not in ("up", "down", "flat"):
            continue
        p.update(base_date=s["last_date"], base_price=s["last_price"],
                 horizon_days=int(p.get("horizon_days") or 7))
        analysis.save_prediction(p, "deepseek")
    report = out.get("report") or ""
    if report:
        db.execute("INSERT INTO reports(created_at,content) VALUES(?,?)",
                   (datetime.now().isoformat(timespec="minutes"), report))
    return report


def run_daily():
    if not enabled():
        return
    try:
        log.info("AI 复盘 %d 条", review_mistakes())
        daily_analysis()
    except Exception as e:
        log.warning("DeepSeek 分析失败: %s", e)


def ask(question, history=None):
    msgs = [{"role": "system", "content": SYSTEM + "\n当前数据:\n" + _ctx_text(analysis.context())}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:4000]})
    msgs.append({"role": "user", "content": question})
    return chat(msgs)
