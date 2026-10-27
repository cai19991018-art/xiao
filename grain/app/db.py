"""SQLite 存储。数据量小, 单文件足够, 备份就是复制 grain.db。"""
import json
import os
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    series TEXT NOT NULL,      -- futures:corn_dce / local:corn:周口
    date   TEXT NOT NULL,      -- YYYY-MM-DD
    price  REAL NOT NULL,
    unit   TEXT,
    source TEXT,               -- sina / manual
    note   TEXT,
    PRIMARY KEY (series, date)
);
CREATE TABLE IF NOT EXISTS weather (
    city   TEXT NOT NULL,
    date   TEXT NOT NULL,
    tmax   REAL, tmin REAL, precip REAL,
    PRIMARY KEY (city, date)
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,          -- stat / deepseek
    region TEXT NOT NULL,          -- 周口
    crop TEXT NOT NULL,            -- corn / wheat
    series TEXT NOT NULL,          -- 用来核验的价格序列
    scale REAL NOT NULL DEFAULT 1, -- 序列价 × scale = 元/斤 (期货折算为 1/2000)
    basis TEXT,                    -- 本地报价 / 期货折算
    base_date TEXT NOT NULL,
    base_price REAL NOT NULL,      -- 元/斤
    horizon_days INTEGER NOT NULL,
    target_date TEXT NOT NULL,
    direction TEXT NOT NULL,       -- up / down / flat
    stance TEXT,                   -- 震荡偏强 等
    low REAL, high REAL,
    confidence REAL,
    path TEXT,                     -- JSON: 每天的预测价
    factors TEXT,                  -- JSON: [{type: plus/minus/neutral, title, detail}]
    watch TEXT,                    -- 需关注
    signals TEXT,                  -- 统计模型信号 momentum=..,seasonal=..
    status TEXT NOT NULL DEFAULT 'open',   -- open / done
    actual_price REAL,
    actual_change_pct REAL,
    correct INTEGER,
    in_range INTEGER,
    review TEXT                    -- 偏差原因
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    prediction_id INTEGER,
    crop TEXT,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    region TEXT,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL            -- JSON
);
"""


def init():
    d = os.path.dirname(config.DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def rows(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def execute(sql, args=()):
    with conn() as c:
        return c.execute(sql, args).lastrowid


def upsert_price(series, date, price, unit=None, source=None, note=None):
    execute(
        "INSERT INTO prices(series,date,price,unit,source,note) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(series,date) DO UPDATE SET price=excluded.price, unit=excluded.unit, "
        "source=excluded.source, note=excluded.note",
        (series, date, price, unit, source, note),
    )


def get(key, default=None):
    r = rows("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(r[0]["value"]) if r else default


def put(key, value):
    execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))
