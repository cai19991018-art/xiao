"""SQLite 存储。数据量小, 单文件足够, 备份就是复制 grain.db。"""
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
    source TEXT NOT NULL,          -- stat / deepseek / gpt
    series TEXT NOT NULL,
    base_date TEXT NOT NULL,
    base_price REAL NOT NULL,
    horizon_days INTEGER NOT NULL,
    target_date TEXT NOT NULL,
    direction TEXT NOT NULL,       -- up / down / flat
    low REAL, high REAL,
    confidence REAL,               -- 0-1
    reasoning TEXT,
    factors TEXT,                  -- 依据的关键因素, 逗号分隔
    status TEXT NOT NULL DEFAULT 'open',   -- open / done
    actual_price REAL,
    actual_change_pct REAL,
    correct INTEGER,
    in_range INTEGER,
    review TEXT                    -- 复盘结论
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    prediction_id INTEGER,
    crop TEXT,
    content TEXT NOT NULL,
    tags TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    content TEXT NOT NULL          -- AI 每日分析报告 (Markdown)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series TEXT NOT NULL,
    op TEXT NOT NULL,              -- above / below
    value REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    triggered_at TEXT
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
