"""main() 的子命令派发测试(mock serve/serve_http, 不实际运行)."""

import ocr_service


def test_main_dispatches_serve(monkeypatch):
    monkeypatch.setattr(ocr_service, "serve", lambda args: ("serve", args), raising=False)
    assert ocr_service.main(["serve", "--device", "cpu"]) == ("serve", ["--device", "cpu"])


def test_main_dispatches_http(monkeypatch):
    monkeypatch.setattr(ocr_service, "serve_http", lambda args: ("http", args), raising=False)
    assert ocr_service.main(["http", "--port", "18011"]) == ("http", ["--port", "18011"])


class _FakeEngine:
    def process(self, path):
        return [
            {
                "input_path": path,
                "page_index": 0,
                "texts": ["hi", "there"],
                "scores": [1.0, 1.0],
                "boxes": [[0, 0, 1, 1], [0, 1, 1, 2]],
                "polys": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
                "lines": [{"text": "hi", "score": 1.0, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]}],
            }
        ]


def test_main_cli_flow(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ocr_service, "ocr", lambda **kw: _FakeEngine())
    out = tmp_path / "out.txt"
    ec = ocr_service.main([str(out)])
    assert ec == 0
    text = capsys.readouterr().out
    assert "总计识别 2 行文本" in text
    assert "hi" in text
