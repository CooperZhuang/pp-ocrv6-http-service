"""HTTP 端点契约测试(TestClient + FakeWorker, 不加载真实模型)."""

import datetime
import logging

import ocr_service
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from conftest import FakeWorker


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["device"] == "cpu"
    assert body["engine"] == "onnxruntime"
    assert body["model_loaded"] is False


def test_ocr_single_file(client):
    r = client.post("/ocr", files=[("files", ("a.png", b"fake-png", "image/png"))])
    assert r.status_code == 200
    res = r.json()["results"]
    assert len(res) == 1
    assert res[0]["ok"] is True
    assert res[0]["filename"] == "a.png"
    assert res[0]["pages"][0]["texts"] == ["电子发票"]


def test_ocr_multi_files_preserve_order(client):
    files = [
        ("files", ("a.png", b"fake-png-a", "image/png")),
        ("files", ("b.png", b"fake-png-b", "image/png")),
    ]
    r = client.post("/ocr", files=files)
    assert r.status_code == 200
    results = r.json()["results"]
    assert [x["filename"] for x in results] == ["a.png", "b.png"]
    assert all(x["ok"] for x in results)


def test_ocr_unsupported_extension(client):
    r = client.post("/ocr", files=[("files", ("note.md", b"# hi", "text/markdown"))])
    assert r.status_code == 200  # 单项失败不中断整批, HTTP 仍 200
    res = r.json()["results"][0]
    assert res["ok"] is False
    assert "unsupported extension" in res["error"]
    # 不支持扩展名的文件不触发推理, model_loaded 保持 False
    assert client.get("/health").json()["model_loaded"] is False


def test_ocr_missing_files_returns_422(client):
    r = client.post("/ocr")
    assert r.status_code == 422


def test_ocr_worker_error_per_file():
    app = ocr_service.build_app(FakeWorker(fail=True))
    with TestClient(app) as c:
        r = c.post("/ocr", files=[("files", ("a.png", b"x", "image/png"))])
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["ok"] is False
    assert "boom" in res["error"]


def test_ocr_mixed_batch_tolerates_one_failure():
    app = ocr_service.build_app(FakeWorker(fail=True))
    with TestClient(app) as c:
        r = c.post(
            "/ocr",
            files=[
                ("files", ("mk.md", b"# x", "text/markdown")),
                ("files", ("a.png", b"x", "image/png")),
            ],
        )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["ok"] is False and "unsupported extension" in results[0]["error"]
    assert results[1]["ok"] is False and "boom" in results[1]["error"]


# ----------------------------------------------------------------------
# /health 运行计数: 区分「请求根本没到」与「到了但连接被重置/处理异常」
# ----------------------------------------------------------------------
def test_health_counters_start_at_zero(client):
    body = client.get("/health").json()
    assert body["requests_total"] == 0
    assert body["requests_failed"] == 0
    assert body["last_request_ts"] is None
    assert body["last_failure_ts"] is None
    assert body["last_failure_reason"] is None
    # /health 自身不计入 /ocr 请求数
    client.get("/health")
    assert client.get("/health").json()["requests_total"] == 0


def test_health_counters_track_ocr_requests(client):
    r = client.post("/ocr", files=[("files", ("a.png", b"fake-png", "image/png"))])
    assert r.status_code == 200
    body = client.get("/health").json()
    assert body["requests_total"] == 1
    assert body["requests_failed"] == 0
    # last_request_ts 是带时区的 ISO 8601 字符串
    ts = datetime.datetime.fromisoformat(body["last_request_ts"])
    assert ts.tzinfo is not None
    assert body["last_failure_ts"] is None


def test_health_counts_422_as_arrival_but_not_failure(client):
    """缺 files 的 422 说明请求到了并被处理, 不算请求级失败(否则无法区分是否到达)."""
    assert client.post("/ocr").status_code == 422
    body = client.get("/health").json()
    assert body["requests_total"] == 1
    assert body["requests_failed"] == 0


def test_health_counts_5xx_response_as_failure():
    stats = ocr_service._RequestStats()
    app = FastAPI()
    ocr_service._install_request_stats(app, stats)

    @app.post("/ocr")
    def _fail():
        return JSONResponse({"ok": False}, status_code=503)

    with TestClient(app) as c:
        r = c.post("/ocr", files=[("files", ("a.png", b"x", "image/png"))])
    assert r.status_code == 503
    snap = stats.snapshot()
    assert snap["requests_total"] == 1
    assert snap["requests_failed"] == 1
    assert "HTTP 503" in snap["last_failure_reason"]


def test_client_disconnect_counts_and_logs_warning(caplog):
    """客户端上传中/处理中断开: 计数 +1 且留下 warning, 不再「日志空白」."""
    stats = ocr_service._RequestStats()
    app = FastAPI()
    ocr_service._install_request_stats(app, stats)

    @app.post("/ocr")
    async def _client_gone():
        raise ClientDisconnect()

    with (
        caplog.at_level(logging.WARNING, logger="ocr_service"),
        TestClient(app, raise_server_exceptions=False) as c,
    ):
        r = c.post("/ocr", files=[("files", ("a.png", b"x", "image/png"))])

    assert r.status_code == 500
    snap = stats.snapshot()
    assert snap["requests_total"] == 1
    assert snap["requests_failed"] == 1
    assert "ClientDisconnect" in snap["last_failure_reason"]
    assert any("断开连接" in rec.getMessage() for rec in caplog.records)


def test_ocr_debug_logs_file_count_and_size(client, caplog):
    """log_level=debug 时 /ocr 进入记文件数、每个文件记大小(排障用)."""
    with caplog.at_level(logging.DEBUG, logger="ocr_service"):
        client.post("/ocr", files=[("files", ("a.png", b"12345", "image/png"))])
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("文件数=1" in m for m in messages)
    assert any("a.png" in m and "5 字节" in m for m in messages)
