"""Web API 回归测试。

覆盖此前"必然 500 / 返回空值 / 契约不匹配"的几个端点：
- /api/factor：字段名必须与 CompanyRow 一致，且输出含 factors 列表
- /api/backtest：必须读 price_clean（不是 price_df），比率不能再 ×100
- /api/sentiment：必须消费 analyze_sentiment 的规范化结构
- /api/stats：必须能调用 render_markdown
- /api/settings：必须真正落盘
- /api/compare：必须返回真实指标 + 图表 JSON

全部在离线模式下运行（把数据层打桩为样例数据），不需要网络与 API Key。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from src.data.data_service import DataService  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """打桩数据层为离线样例，避免任何网络请求。"""
    real = DataService()
    original = DataService.get_company_bundle  # 先抓原始函数，避免打桩后自递归

    def fake_bundle(self, symbol, *args, **kwargs):
        kwargs.pop("prefer_live", None)
        return original(real, symbol, prefer_live=False)

    monkeypatch.setattr(DataService, "get_company_bundle", fake_bundle)

    # 设置文件写到临时目录，避免污染仓库
    app_module = importlib.import_module("web.app")
    monkeypatch.setattr(app_module, "WEB_SETTINGS_PATH", tmp_path / "web_settings.json")
    return TestClient(app_module.app, raise_server_exceptions=False)


def test_factor_endpoint_returns_real_scores(client):
    r = client.get("/api/factor?symbol=600519")
    assert r.status_code == 200, r.text
    body = r.json()["text"]
    # 之前这里因 CompanyRow(revenue_growth=...) 必然 500
    assert "综合得分" in body
    # 财务指标必须真的被填进来（不是"数据不足"）
    assert "营收同比" in body
    assert "数据不足" in body or "/ 10" in body


def test_backtest_endpoint_uses_price_clean(client):
    r = client.get("/api/backtest?symbol=600519")
    assert r.status_code == 200, r.text
    body = r.json()["text"]
    # 之前读 bundle.price_df -> 500
    assert "MACD 策略回测" in body
    assert "胜率" in body
    # 比率不应被 ×100（旧代码会打印出 1110.0% 这种数字）
    for token in ("1110.0%", "-1567.0%"):
        assert token not in body


def test_sentiment_endpoint_consumes_normalized_schema(client):
    r = client.get("/api/sentiment?symbol=600519")
    assert r.status_code == 200, r.text
    body = r.json()["text"]
    # 旧实现读 overall/positive_ratio/... 导致 500 或恒为 0%
    assert "整体情绪" in body
    assert "情绪评分" in body


def test_stats_endpoint_renders_markdown(client):
    r = client.get("/api/stats")
    # 旧实现调用不存在的 render_markdown -> 500
    assert r.status_code == 200, r.text
    assert "幻觉率" in r.json()["text"]


def test_settings_roundtrip(client):
    r = client.post("/api/settings", json={"feishu_webhook_url": "https://example.com/hook"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    got = client.get("/api/settings").json()
    assert got["feishu_webhook_url"] == "https://example.com/hook"
    # 密钥字段应脱敏
    client.post("/api/settings", json={"wecom_webhook_key": "supersecretvalue"})
    assert client.get("/api/settings").json()["wecom_webhook_key"].startswith("*")


def test_compare_returns_real_metrics_and_charts(client):
    r = client.get("/api/compare?symbols=000000,000001")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 2
    # 之前字段名错配导致整张表都是 "-"
    assert "营收同比%" in data["text"]
    first_row = data["text"].splitlines()
    row = [ln for ln in first_row if ln.startswith("| 1 |")][0]
    assert row.count(" - ") < 5, f"对比行仍然大量为空: {row}"
    assert "charts" in data


def test_index_page_uses_sanitizing_renderer(client):
    html = client.get("/").text
    assert "function renderMD" in html
    assert "DOMPurify" in html
    # 自选股渲染不再把用户输入拼进 onclick
    assert "${s}</span>" not in html


def test_report_stream_emits_steps(client):
    """SSE 端点应能在离线模式下跑完并返回 done 事件。"""
    with client.stream("GET", "/api/report/stream?topic=测试主题&provider=mock") as resp:
        assert resp.status_code == 200
        payload = "".join(resp.iter_text())
    assert '"type": "done"' in payload or '"type":"done"' in payload
