import math
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import analysis, config, db, fetchers

H = {"X-API-Key": "k"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "API_KEY", "k")
    monkeypatch.setattr(fetchers, "fetch_futures", lambda: 0)
    monkeypatch.setattr(fetchers, "fetch_weather", lambda: 0)
    from app import main
    monkeypatch.setattr(main, "job_all", lambda: None)
    db.init()
    with TestClient(main.app) as c:
        yield c


def seed(series="futures:corn_dce", days=800, end=None):
    end = end or date.today()
    for i in range(days):
        d = end - timedelta(days=days - 1 - i)
        db.upsert_price(series, d.isoformat(), 2300 + 50 * math.sin(i / 30) + i * 0.1)


def test_parse_kline():
    assert fetchers.parse_kline([{"d": "2026-01-02", "c": "2300"}]) == [("2026-01-02", 2300.0)]
    assert fetchers.parse_kline([{"date": "2026-01-02", "close": 450.5}]) == [("2026-01-02", 450.5)]
    assert fetchers._jsonp('var x=([{"d":"2026-01-02","c":"1"}]);') == [{"d": "2026-01-02", "c": "1"}]


def test_auth(client):
    assert client.get("/api/context").status_code == 401
    assert client.get("/api/context", headers=H).status_code == 200


def test_local_price_and_context(client):
    r = client.post("/api/prices", headers=H, json={"crop": "wheat", "place": "周口", "price": 1.21})
    assert r.json()["series"] == "local:wheat:周口"
    client.post("/api/notes", headers=H, json={"content": "农户惜售"})
    c = client.get("/api/context", headers=H).json()
    assert c["series"][0]["name"] == "周口小麦收购价"
    assert c["field_notes"][0]["content"] == "农户惜售"


def test_stat_prediction_and_evaluate(client):
    # 历史数据截止 40 天前, 先预测, 再补上后面的数据, 验证到期复盘
    seed(end=date.today() - timedelta(days=40))
    assert analysis.run_stat_predictions() == 3
    assert analysis.run_stat_predictions() == 0  # 同一天不重复
    seed(days=40, end=date.today())
    assert analysis.evaluate_due() == 3
    done = db.rows("SELECT * FROM predictions WHERE status='done'")
    assert all(p["correct"] in (0, 1) and p["review"] for p in done)
    acc = analysis.accuracy()
    assert sum(a["n"] for a in acc) == 3
    w = analysis.signal_weights()
    assert 0 < w["momentum"] < 1


def test_gpt_prediction_review_loop(client):
    seed(days=60, end=date.today() - timedelta(days=10))
    r = client.post("/api/predictions", headers=H, json={
        "series": "futures:corn_dce", "horizon_days": 7, "direction": "up",
        "confidence": 0.6, "reasoning": "测试"}).json()
    # 后续价格大跌, GPT 预测必然错误
    base = r["base_price"]
    for i in range(10):
        d = date.today() - timedelta(days=9 - i)
        db.upsert_price("futures:corn_dce", d.isoformat(), base * 0.9)
    analysis.evaluate_due()
    c = client.get("/api/context", headers=H).json()
    assert [p["id"] for p in c["needs_review"]] == [r["id"]]
    client.post(f"/api/predictions/{r['id']}/review", headers=H,
                json={"review": "忽视拍卖", "lesson": "拍卖期间看空"})
    c = client.get("/api/context", headers=H).json()
    assert c["needs_review"] == []
    assert c["lessons"][0]["content"] == "拍卖期间看空"
    assert c["lessons"][0]["crop"] == "corn"


def test_unknown_series_rejected(client):
    r = client.post("/api/predictions", headers=H, json={
        "series": "nope", "horizon_days": 7, "direction": "up", "confidence": 0.5, "reasoning": "x"})
    assert r.status_code == 400


def test_alerts(client):
    seed(days=40)
    client.post("/api/alerts", headers=H, json={"series": "futures:corn_dce", "op": "above", "value": 1})
    assert len(analysis.check_alerts()) == 1
    assert analysis.check_alerts() == []  # 触发一次后关闭


def test_openapi_for_gpt(client):
    s = client.get("/openapi.json").json()
    ops = [o["operationId"] for p in s["paths"].values() for o in p.values()]
    assert "getContext" in ops and len(ops) <= 30
    assert s["servers"][0]["url"] == config.PUBLIC_URL
