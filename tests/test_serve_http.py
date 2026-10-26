"""serve_http 的应用构建/配置测试(用 mock 替换 uvicorn.run, 不真正启动监听)."""

import json
import logging

import ocr_service


def test_serve_http_builds_app_with_routes(monkeypatch):
    saved = list(logging.getLogger().handlers)
    cap = {}

    def _fake_run(app, **kwargs):  # noqa: ARG002
        cap.update(app=app)

    try:
        monkeypatch.setattr("uvicorn.run", _fake_run)
        rc = ocr_service.serve_http(["--port", "18099", "--idle-unload", "5"])
        app = cap["app"]
        paths = {r.path for r in app.routes}
        assert "/health" in paths
        assert "/ocr" in paths
        assert rc == 0
    finally:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)


def test_serve_http_passes_det_limit_to_worker(monkeypatch):
    """HTTP 启动参数 --det-limit-side-len 传给推理子进程(票据小字/手写识别调参用)."""
    saved = list(logging.getLogger().handlers)
    cap = {}

    class _FakeWorker:
        def __init__(self, **kwargs):
            cap.update(kwargs)

        def unload_if_idle(self):
            return False

        def shutdown(self):
            pass

    try:
        monkeypatch.setattr(ocr_service, "_WorkerEngine", _FakeWorker)
        monkeypatch.setattr("uvicorn.run", lambda app, **kw: None)  # noqa: ARG005
        rc = ocr_service.serve_http(
            ["--port", "18099", "--idle-unload", "0", "--det-limit-side-len", "1600"])
        assert rc == 0
        assert cap["det_limit_side_len"] == 1600
        assert cap["engine"] == "onnxruntime"
    finally:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)


def _serve_http_capture(monkeypatch, argv):
    """跑一次 serve_http(mock 掉 worker 与 uvicorn.run), 返回 uvicorn.run 收到的 kwargs."""
    saved = list(logging.getLogger().handlers)
    cap = {}

    class _FakeWorker:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        def unload_if_idle(self):
            return False

        def shutdown(self):
            pass

    try:
        monkeypatch.setattr(ocr_service, "_WorkerEngine", _FakeWorker)
        monkeypatch.setattr("uvicorn.run", lambda app, **kw: cap.update(kw))  # noqa: ARG005
        assert ocr_service.serve_http(argv) == 0
    finally:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)
    return cap


def test_serve_http_default_timeout_keep_alive_is_120(monkeypatch):
    """不配置时默认 120s(uvicorn 自带默认 5s 会在空闲复用连接时发 FIN, 见 README)."""
    cap = _serve_http_capture(monkeypatch, ["--port", "18099", "--idle-unload", "0"])
    assert cap["timeout_keep_alive"] == ocr_service.TIMEOUT_KEEP_ALIVE_DEFAULT == 120


def test_serve_http_timeout_keep_alive_from_config(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"timeout_keep_alive": 45}), encoding="utf-8")
    cap = _serve_http_capture(
        monkeypatch,
        ["--config", str(cfg), "--port", "18099", "--idle-unload", "0"],
    )
    assert cap["timeout_keep_alive"] == 45.0


def test_serve_http_cli_timeout_keep_alive_overrides_config(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"timeout_keep_alive": 45}), encoding="utf-8")
    cap = _serve_http_capture(
        monkeypatch,
        [
            "--config",
            str(cfg),
            "--port",
            "18099",
            "--idle-unload",
            "0",
            "--timeout-keep-alive",
            "300",
        ],
    )
    assert cap["timeout_keep_alive"] == 300.0


def test_serve_http_invalid_config_timeout_keep_alive_falls_back(monkeypatch, tmp_path):
    """config.json 里写 0/负数 -> 退回默认 120, 不把非法值交给 uvicorn."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"timeout_keep_alive": 0}), encoding="utf-8")
    cap = _serve_http_capture(
        monkeypatch,
        ["--config", str(cfg), "--port", "18099", "--idle-unload", "0"],
    )
    assert cap["timeout_keep_alive"] == ocr_service.TIMEOUT_KEEP_ALIVE_DEFAULT


def test_serve_http_invalid_cli_timeout_keep_alive_falls_back(monkeypatch):
    cap = _serve_http_capture(
        monkeypatch,
        ["--port", "18099", "--idle-unload", "0", "--timeout-keep-alive", "-5"],
    )
    assert cap["timeout_keep_alive"] == ocr_service.TIMEOUT_KEEP_ALIVE_DEFAULT
