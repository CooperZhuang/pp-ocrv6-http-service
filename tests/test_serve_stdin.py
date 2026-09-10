"""serve() 的 stdin 桥行为测试(不加载真实模型)."""

import io
import json

import ocr_service


def _run_serve(stdin_text, argv=()):
    stdin = io.StringIO(stdin_text)
    stdout = io.StringIO()
    prev_in, prev_out = ocr_service.sys.stdin, ocr_service.sys.stdout
    ocr_service.sys.stdin = stdin
    ocr_service.sys.stdout = stdout
    try:
        code = ocr_service.serve(list(argv))
    finally:
        ocr_service.sys.stdin = prev_in
        ocr_service.sys.stdout = prev_out
    return code, stdout.getvalue()


def test_serve_writes_ready_handshake_then_ok():
    # 空路径 -> ok:false(不触发模型), 随后 shutdown 退出
    code, out = _run_serve('{"id": 1, "path": ""}\n{"shutdown": true}\n')
    assert code == 0
    lines = [json.loads(line) for line in out.strip().splitlines()]
    assert lines[0] == {"id": 0, "ready": True}
    assert lines[1]["ok"] is False
    assert "empty path" in lines[1]["error"]


def test_serve_ignores_blank_and_invalid_lines():
    code, out = _run_serve('\n\n{"shutdown": true}\n')
    assert code == 0
    lines = [json.loads(line) for line in out.strip().splitlines()]
    assert lines[0] == {"id": 0, "ready": True}


def test_serve_retains_request_id_in_error():
    code, out = _run_serve('{"id": 42, "path": ""}\n{"shutdown": true}\n')
    assert code == 0
    lines = [json.loads(line) for line in out.strip().splitlines()]
    assert lines[1]["id"] == 42


def test_serve_forwards_det_limit_to_engine(monkeypatch):
    """--det-limit-side-len 透传给引擎(HTTP 推理子进程靠这条参数提高检测分辨率)."""
    seen = {}

    class _FakeEngine:
        def process(self, path):  # noqa: ARG002
            return []

    def _fake_ocr(**kwargs):
        seen.update(kwargs)
        return _FakeEngine()

    monkeypatch.setattr(ocr_service, "ocr", _fake_ocr)
    code, _ = _run_serve('{"shutdown": true}\n', argv=["--det-limit-side-len", "1600"])
    assert code == 0
    assert seen.get("text_det_limit_side_len") == 1600
    assert seen.get("engine") == "onnxruntime"


def test_serve_omits_det_limit_when_not_given(monkeypatch):
    """不传该参数时不带进引擎参数 -> 沿用 PaddleOCR 默认 960."""
    seen = {}

    class _FakeEngine:
        def process(self, path):  # noqa: ARG002
            return []

    monkeypatch.setattr(ocr_service, "ocr",
                        lambda **kw: (seen.update(kw), _FakeEngine())[1])
    _run_serve('{"shutdown": true}\n')
    assert "text_det_limit_side_len" not in seen
