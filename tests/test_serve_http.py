"""serve_http 的应用构建/配置测试(用 mock 替换 uvicorn.run, 不真正启动监听)."""

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
