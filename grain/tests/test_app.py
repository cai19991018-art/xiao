import math
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import analysis, config, db, fetchers, llm

H = {"X-API-Key": "k"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "API_KEY", "k")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(fetchers, "fetch_futures", lambda: 0)
    monkeypatch.setattr(fetchers, "fetch_weather", lambda: 0)
    from app import main
    monkeypatch.setattr(main, "job_all", lambda: None)
    db.init()
    with TestClient(main.app) as c:
        yield c


def seed(series="futures:corn_dce", days=800, end=None, base=2300):
    end = end or date.today()
    for i in range(days):
        d = end - timedelta(days=days - 1 - i)
        db.upsert_price(series, d.isoformat(), base + base / 50 * math.sin(i / 30) + i * 0.1)


def test_parse_kline():
    assert fetchers.parse_kline([{"d": "2026-01-02", "c": "2300"}]) == [("2026-01-02", 2300.0)]
    assert fetchers.parse_kline([{"date": "2026-01-02", "close": 450.5}]) == [("2026-01-02", 450.5)]
    assert fetchers._jsonp('var x=([{"d":"2026-01-02","c":"1"}]);') == [{"d": "2026-01-02", "c": "1"}]


def test_auth(client):
    assert client.get("/api/meta").status_code == 401
    assert client.get("/api/meta", headers=H).json()["prefs"]["region"] == "周口"


def test_resolve_falls_back_to_futures_then_local(client):
    seed(days=40)
    m = client.get("/api/market?region=周口&crop=corn", headers=H).json()
    assert m["basis"] == "期货折算" and 1.0 < m["price"] < 1.3  # 元/吨 ÷ 2000
    for i in range(20):
        client.post("/api/prices", headers=H, json={
            "crop": "corn", "region": "周口", "price": 1.10 + i / 1000,
            "date": (date.today() - timedelta(days=19 - i)).isoformat()})
    m = client.get("/api/market?region=周口&crop=corn", headers=H).json()
    assert m["basis"] == "本地报价" and m["price"] == 1.119
    assert client.get("/api/market?region=火星&crop=corn", headers=H).status_code == 400


def test_prefs(client):
    r = client.post("/api/prefs", headers=H, json={"region": "驻马店", "crop": "wheat", "unit": "ton"})
    assert r.json()["region"] == "驻马店"
    assert client.get("/api/meta", headers=H).json()["prefs"]["crop"] == "wheat"


def test_stat_analysis_and_evaluate(client):
    seed(end=date.today() - timedelta(days=10))
    r = client.post("/api/analysis", headers=H, json={"region": "周口", "crop": "corn", "horizon": 7}).json()
    a = r["analysis"]
    assert a["source"] == "stat" and len(a["path"]) == 7 and a["factors"] and a["stance"]
    assert a["basis"] == "期货折算" and len(r["history"]) > 0
    # 同一天重复生成不重复存
    client.post("/api/analysis", headers=H, json={"region": "周口", "crop": "corn", "horizon": 7})
    assert len(db.rows("SELECT * FROM predictions")) == 1
    seed(days=10, end=date.today())
    assert analysis.evaluate_due() == 1
    rv = client.get("/api/reviews?region=周口&crop=corn", headers=H).json()
    assert rv["checked"] == 1 and rv["pending"] == 0
    assert rv["items"][0]["actual_price"] is not None


def test_not_enough_data(client):
    r = client.post("/api/analysis", headers=H, json={"region": "周口", "crop": "wheat", "horizon": 3})
    assert r.status_code == 400


def test_deepseek_analysis_and_review(client, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    seed(days=60, end=date.today() - timedelta(days=10))

    def fake_chat(msgs, timeout=120):
        if "mistakes" in msgs[-1]["content"]:
            pid = db.rows("SELECT id FROM predictions WHERE source='deepseek'")[0]["id"]
            return {"reviews": [{"id": pid, "review": "实际供应压力高于预期", "lesson": "新粮集中上市时偏谨慎"}]}
        return {"direction": "up", "stance": "震荡偏强", "low": 1.1, "high": 1.2, "confidence": 0.6,
                "path": [1.15, 1.16], "factors": [{"type": "plus", "title": "企业到货减少", "detail": "d"},
                                                  {"type": "bad", "title": "新粮上市", "detail": "d"}],
                "watch": "若企业连续降价，重新评估"}
    monkeypatch.setattr(llm, "chat", fake_chat)
    a = client.post("/api/analysis", headers=H, json={"region": "周口", "crop": "corn", "horizon": 7}).json()["analysis"]
    assert a["source"] == "deepseek" and a["stance"] == "震荡偏强"
    assert len(a["path"]) == 7  # 不足 7 个自动补齐
    assert a["factors"][1]["type"] == "neutral"
    base = a["base_price"] * 2000
    for i in range(10):
        db.upsert_price("futures:corn_dce", (date.today() - timedelta(days=9 - i)).isoformat(), base * 0.9)
    analysis.evaluate_due()
    assert llm.review_mistakes() == 1
    rv = client.get("/api/reviews", headers=H).json()
    assert [p["source"] for p in rv["items"]] == ["deepseek"]  # 有 AI 记录时只展示 AI
    assert rv["items"][0]["review"] == "实际供应压力高于预期"
    assert rv["lessons"][0]["content"] == "新粮集中上市时偏谨慎"


def test_deepseek_failure_falls_back(client, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    seed(days=60)

    def boom(*a, **k):
        raise RuntimeError("timeout")
    monkeypatch.setattr(llm, "chat", boom)
    a = client.post("/api/analysis", headers=H, json={"region": "周口", "crop": "corn", "horizon": 3}).json()["analysis"]
    assert a["source"] == "stat"
    assert client.get("/api/status", headers=H).json()["deepseek"]["last_error"]["error"] == "timeout"


def test_status_and_auto_job(client, monkeypatch):
    from app import main
    seed(days=60)
    main.job_daily()
    s = client.get("/api/status", headers=H).json()
    assert s["market"]["ok"] and s["deepseek"]["ok"] is False and s["auto"]["last_run"]
    assert len(db.rows("SELECT * FROM predictions")) == 2  # 玉米 3/7 天; 小麦无数据跳过
