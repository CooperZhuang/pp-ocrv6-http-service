"""HTTP 端点契约测试(TestClient + FakeWorker, 不加载真实模型)."""

import ocr_service
from fastapi.testclient import TestClient

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
