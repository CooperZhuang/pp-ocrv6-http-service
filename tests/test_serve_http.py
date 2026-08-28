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
