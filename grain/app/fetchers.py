"""联网采集: 新浪期货日线 + Open-Meteo 天气。失败只记日志, 不影响其他任务。"""
import json
import logging
import re
import time

import requests

from . import config, db

log = logging.getLogger("fetch")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
SINA = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_x="


def _jsonp(text):
    m = re.search(r"\((\[.*\])\)", text, re.S)
    return json.loads(m.group(1)) if m else []


def parse_kline(items):
    """新浪内盘返回 {d,c,...}, 外盘返回 {date,close,...}; 统一成 [(date, close)]。"""
    out = []
    for it in items:
        d = it.get("d") or it.get("date")
        c = it.get("c") or it.get("close")
        if d and c not in (None, ""):
            out.append((d[:10], float(c)))
    return out


def fetch_futures(days=400):
    n = 0
    for series, (code, kind, _name, unit) in config.FUTURES.items():
        if kind == "inner":
            url = f"{SINA}/InnerFuturesNewService.getDailyKLine?symbol={code}"
        else:
            url = f"{SINA}/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={code}&_={int(time.time())}&source=web"
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            for d, c in parse_kline(_jsonp(r.text))[-days:]:
                db.upsert_price(series, d, c, unit, "sina")
                n += 1
        except Exception as e:  # 单个品种失败不影响其他
            log.warning("期货 %s 采集失败: %s", series, e)
    log.info("期货采集完成, 写入 %d 条", n)
    return n


def fetch_weather():
    n = 0
    for city, (lat, lon) in config.HENAN_CITIES.items():
        try:
            r = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon, "timezone": config.TZ,
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                    "past_days": 7, "forecast_days": 10,
                },
                timeout=20,
            )
            r.raise_for_status()
            d = r.json()["daily"]
            for i, date in enumerate(d["time"]):
                db.execute(
                    "INSERT OR REPLACE INTO weather(city,date,tmax,tmin,precip) VALUES(?,?,?,?,?)",
                    (city, date, d["temperature_2m_max"][i], d["temperature_2m_min"][i],
                     d["precipitation_sum"][i]),
                )
                n += 1
        except Exception as e:
            log.warning("天气 %s 采集失败: %s", city, e)
    return n


def push(title, content):
    """PushPlus 微信推送, 没配 token 就跳过。"""
    if not config.PUSHPLUS_TOKEN:
        return False
    try:
        requests.post("https://www.pushplus.plus/send", timeout=15,
                      json={"token": config.PUSHPLUS_TOKEN, "title": title, "content": content})
        return True
    except Exception as e:
        log.warning("推送失败: %s", e)
        return False
